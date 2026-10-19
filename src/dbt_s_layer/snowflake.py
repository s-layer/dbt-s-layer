"""
Render a semantic view as the body of a Snowflake `dbt_semantic_view` model (SEMVW-02).

The Snowflake-Labs `dbt_semantic_view` package materialises a model by running
`create or replace semantic view <relation>` followed by the model's SQL, so the
body is the `CREATE SEMANTIC VIEW` clause list: TABLES, RELATIONSHIPS,
DIMENSIONS, METRICS and COMMENT, in Snowflake's required order
(04-RESEARCH.md §1, §2). Tables are `{{ ref() }}` calls, so dbt sees the DAG
edges (SEMVW-05).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.semantic_views import MetricRef, scan_sql
from dbt_s_layer.sentinels import escape_jinja, postflight

if TYPE_CHECKING:
    from dbt_s_layer.semantic_views import (
        DerivedMetricSpec,
        DimensionSpec,
        MetricSpec,
        SemanticViewSpec,
        TableSpec,
    )

PACKAGE = "dbt_semantic_view"
# In manifest.json `macros` when the package is installed (04-RESEARCH §4).
MATERIALIZATION_MACRO = "macro.dbt_semantic_view.materialization_semantic_view_snowflake"
CONFIG = "{{ config(materialized='semantic_view', static_analysis='off') }}"

# https://docs.snowflake.com/en/sql-reference/reserved-keywords (fetched 2026-10-19).
RESERVED_WORDS = frozenset(
    [
        "ACCOUNT",
        "ALL",
        "ALTER",
        "AND",
        "ANY",
        "AS",
        "BETWEEN",
        "BY",
        "CASE",
        "CAST",
        "CHECK",
        "COLUMN",
        "CONNECT",
        "CONNECTION",
        "CONSTRAINT",
        "CREATE",
        "CROSS",
        "CURRENT",
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "CURRENT_USER",
        "DATABASE",
        "DELETE",
        "DISTINCT",
        "DROP",
        "ELSE",
        "EXISTS",
        "FALSE",
        "FOLLOWING",
        "FOR",
        "FROM",
        "FULL",
        "GRANT",
        "GROUP",
        "GSCLUSTER",
        "HAVING",
        "ILIKE",
        "IN",
        "INCREMENT",
        "INNER",
        "INSERT",
        "INTERSECT",
        "INTO",
        "IS",
        "ISSUE",
        "JOIN",
        "LATERAL",
        "LEFT",
        "LIKE",
        "LOCALTIME",
        "LOCALTIMESTAMP",
        "MINUS",
        "NATURAL",
        "NOT",
        "NULL",
        "OF",
        "ON",
        "OR",
        "ORDER",
        "ORGANIZATION",
        "QUALIFY",
        "REGEXP",
        "REVOKE",
        "RIGHT",
        "RLIKE",
        "ROW",
        "ROWS",
        "SAMPLE",
        "SCHEMA",
        "SELECT",
        "SET",
        "SOME",
        "START",
        "TABLE",
        "TABLESAMPLE",
        "THEN",
        "TO",
        "TRIGGER",
        "TRUE",
        "TRY_CAST",
        "UNION",
        "UNIQUE",
        "UPDATE",
        "USING",
        "VALUES",
        "VIEW",
        "WHEN",
        "WHENEVER",
        "WHERE",
        "WINDOW",
        "WITH",
    ]
)
# Words of the CREATE SEMANTIC VIEW grammar. Quoting them costs nothing (a quoted
# upper-case name is the name Snowflake folds an unquoted one to) and keeps an
# element named `comment` or `metrics` from reading as a keyword.
_DDL_WORDS = frozenset(
    [
        "ADDITIVE",
        "ASC",
        "ASOF",
        "COMMENT",
        "DESC",
        "DIMENSIONS",
        "FACTS",
        "KEY",
        "LABELS",
        "METRICS",
        "NON",
        "NULLS",
        "PRIMARY",
        "PRIVATE",
        "PUBLIC",
        "REFERENCES",
        "RELATIONSHIPS",
        "SYNONYMS",
        "TABLES",
        "TAG",
    ]
)

# Keywords inside expressions that the reserved list leaves out: `CASE ... END` is no
# reference to an element named `end`.
_EXPRESSION_KEYWORDS = RESERVED_WORDS | {"ASC", "DESC", "END", "INTERVAL", "NULLS"}

_SIMPLE_AGGREGATIONS = {
    "sum": "SUM({})",
    "min": "MIN({})",
    "max": "MAX({})",
    "average": "AVG({})",
    "count": "COUNT({})",
    "count_distinct": "COUNT(DISTINCT {})",
    "sum_boolean": "SUM(CASE WHEN {} THEN 1 ELSE 0 END)",
}


def quote_identifier(name: str) -> str:
    """Return `name` unquoted, or as an upper-case quoted identifier when it is a keyword."""
    upper = name.upper()
    if upper in RESERVED_WORDS or upper in _DDL_WORDS:
        return f'"{upper}"'
    return name


def sql_string(text: str) -> str:
    """Return a Snowflake single-quoted string literal for `text`, newlines escaped."""
    escaped = text.replace("\\", "\\\\").replace("'", "''")
    escaped = escaped.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    return f"'{escaped}'"


def _comment(text: str | None) -> str:
    return "" if text is None else f" COMMENT = {escape_jinja(sql_string(text))}"


def _qualified(table: str, name: str) -> str:
    return f"{quote_identifier(table)}.{quote_identifier(name)}"


def _clause(keyword: str, items: list[str]) -> str:
    return f"{keyword} (\n" + ",\n".join(items) + "\n)\n"


def _table(table: TableSpec) -> str:
    lines = [f"  {quote_identifier(table.alias)} AS {table.ref}"]
    if table.primary_key is not None:
        lines.append(f"    PRIMARY KEY ({table.primary_key})")
    lines += [f"    UNIQUE ({column})" for column in table.unique_keys]
    if table.comment is not None:
        lines.append(f"   {_comment(table.comment)}")
    return "\n".join(lines)


def _dimension(dimension: DimensionSpec) -> str:
    name = _qualified(dimension.table, dimension.name)
    return f"  {name} AS {escape_jinja(dimension.expr)}{_comment(dimension.comment)}"


def _aggregate(view: str, metric: MetricSpec) -> str:
    expr = escape_jinja(metric.expr)
    if metric.agg != "percentile":
        return _SIMPLE_AGGREGATIONS[metric.agg].format(expr)
    percentile = metric.percentile
    if percentile is None:
        raise SemanticViewError(
            f"sem__{view}: percentile metric {metric.name!r} has no percentile value."
        )
    value = repr(percentile.value)
    if percentile.approximate:
        if percentile.discrete:
            raise SemanticViewError(
                f"sem__{view}: metric {metric.name!r} is an approximate discrete percentile, "
                "which Snowflake does not support."
            )
        return f"APPROX_PERCENTILE({expr}, {value})"
    function = "PERCENTILE_DISC" if percentile.discrete else "PERCENTILE_CONT"
    return f"{function}({value}) WITHIN GROUP (ORDER BY {expr})"


def _metric(view: str, metric: MetricSpec) -> str:
    private = "PRIVATE " if metric.private else ""
    non_additive = ""
    if metric.non_additive is not None:
        order = "ASC" if metric.non_additive.latest else "DESC"
        dimension = _qualified(metric.table, metric.non_additive.dimension)
        non_additive = f" NON ADDITIVE BY ({dimension} {order})"
    name = _qualified(metric.table, metric.name)
    aggregate = _aggregate(view, metric)
    return f"  {private}{name}{non_additive} AS {aggregate}{_comment(metric.comment)}"


def _derived(metric: DerivedMetricSpec) -> str:
    expr = "".join(
        _qualified(p.table, p.name) if isinstance(p, MetricRef) else escape_jinja(p)
        for p in metric.parts
    )
    private = "PRIVATE " if metric.private else ""
    return f"  {private}{quote_identifier(metric.name)} AS {expr}{_comment(metric.comment)}"


# --- name shadowing (04-RESEARCH, open risk 2) ------------------------------------


def _references(expr: str, table: str) -> set[str]:
    """Lower-cased names `expr` may resolve against `table`'s own elements."""
    names: set[str] = set()
    tokens = scan_sql(expr)
    previous: str | None = None
    for token in tokens:
        if not token.identifier:
            continue
        same_table = not token.qualified or (previous is not None and previous.lower() == table)
        if same_table and not token.call and token.text.upper() not in _EXPRESSION_KEYWORDS:
            names.add(token.text.lower())
        previous = token.text
    return names


def _is_column(expr: str, name: str, table: str) -> bool:
    text = expr.strip().lower()
    return text in (name.lower(), f"{table}.{name}".lower())


def check_name_shadowing(spec: SemanticViewSpec) -> None:
    """
    Refuse an element whose name would capture a column another element of its table uses.

    Snowflake resolves a bare name to a semantic expression before a column (except
    inside that expression's own definition), so a dimension `amount AS amount * 100`
    would silently change a metric `SUM(amount)`. metricflow resolves every expr
    against the table's columns.
    """
    for table in spec.tables:
        # (kind, name, expr, the expr as the DDL shows it)
        elements: list[tuple[str, str, str, str]] = [
            ("dimension", d.name, d.expr, d.expr) for d in spec.dimensions if d.table == table.alias
        ]
        elements += [
            ("metric", m.name, m.expr, _SIMPLE_AGGREGATIONS.get(m.agg, "{}").format(m.expr))
            for m in spec.metrics
            if m.table == table.alias
        ]
        for kind, name, expr, _ in elements:
            # A metric is an aggregate even over a bare column, so only a dimension that is
            # exactly its column is harmless.
            harmless = kind == "dimension" and _is_column(expr, name, table.alias)
            if harmless or name.upper() in _EXPRESSION_KEYWORDS:
                continue
            for other_kind, other, other_expr, shown in elements:
                if other == name and other_kind == kind:
                    continue
                if name.lower() in _references(other_expr, table.alias):
                    raise SemanticViewError(
                        f"sem__{spec.name}: {kind} {table.alias}.{name} ({expr!r}) has the "
                        f"name of a column that {other_kind} {table.alias}.{other} uses "
                        f"({shown!r}). Snowflake resolves the name to the {kind}, not "
                        f"the column, which would change {other}. Rename the {kind}, or "
                        f"make its expr exactly {name!r}."
                    )


# --- render ---------------------------------------------------------------------------


def render_snowflake(spec: SemanticViewSpec) -> str:
    """Return the model body: `{{ config() }}`, a blank line, and the semantic view clauses."""
    if not spec.dimensions and not spec.metrics and not spec.derived_metrics:
        raise SemanticViewError(
            f"sem__{spec.name}: a semantic view needs at least one dimension or metric."
        )
    check_name_shadowing(spec)
    parts = [_clause("TABLES", [_table(t) for t in spec.tables])]
    if spec.relationships:
        parts.append(
            _clause(
                "RELATIONSHIPS",
                [
                    f"  {quote_identifier(r.name)} AS {quote_identifier(r.child)} "
                    f"({r.child_column}) REFERENCES {quote_identifier(r.parent)} "
                    f"({r.parent_column})"
                    for r in spec.relationships
                ],
            )
        )
    if spec.dimensions:
        parts.append(_clause("DIMENSIONS", [_dimension(d) for d in spec.dimensions]))
    metrics = [_metric(spec.name, m) for m in spec.metrics]
    metrics += [_derived(m) for m in spec.derived_metrics]
    if metrics:
        parts.append(_clause("METRICS", metrics))
    if spec.comment is not None:
        parts.append(f"COMMENT = {escape_jinja(sql_string(spec.comment))}\n")
    body = f"{CONFIG}\n\n" + "".join(parts)
    postflight(body, context=f"sem__{spec.name}.sql")
    return body
