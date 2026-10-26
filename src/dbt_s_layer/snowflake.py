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

from s_layer_semantic_formats import (
    MAX_TAG_LENGTH,
    SpecTooLongError,
    spec_tag_value,
    to_compact_json,
)

from dbt_s_layer.errors import SemanticViewError, SLayerBlockError
from dbt_s_layer.semantic_views import DimensionSpec, MetricRef, SqlToken, scan_sql
from dbt_s_layer.sentinels import escape_jinja, postflight

if TYPE_CHECKING:
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet

    from s_layer_semantic_formats import SLayerBlock

    from dbt_s_layer.semantic_views import (
        DerivedMetricSpec,
        MetricSpec,
        SemanticViewSpec,
        TableSpec,
    )

PACKAGE = "dbt_semantic_view"
# In manifest.json `macros` when the package is installed (04-RESEARCH §4).
MATERIALIZATION_MACRO = "macro.dbt_semantic_view.materialization_semantic_view_snowflake"
CONFIG = "{{ config(materialized='semantic_view', static_analysis='off') }}"
# Where the S_LAYER_* tag objects live: a Jinja expression for '<database>.<schema>'.
TAG_SCHEMA = "var('s_layer_tag_schema', target.database ~ '.' ~ target.schema)"
# dbt_project.yml `on-run-start` entries that create the tag objects (README: "Snowflake tags").
TAG_NAMES = ("S_LAYER_KIND", "S_LAYER_LABEL", "S_LAYER_SPEC")
TAG_HOOKS = (
    f'"create schema if not exists {{{{ {TAG_SCHEMA} }}}}"',
    *(f'"create tag if not exists {{{{ {TAG_SCHEMA} }}}}.{name}"' for name in TAG_NAMES),
)

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

# Functions whose first argument is a date or time part keyword, as in
# `DATE_TRUNC(month, x)`; `month` there is no column.
_DATE_PART_FIRST = frozenset(
    [
        "DATEADD",
        "DATEDIFF",
        "DATE_PART",
        "DATE_TRUNC",
        "TIMEADD",
        "TIMEDIFF",
        "TIMESTAMPADD",
        "TIMESTAMPDIFF",
    ]
)
# The second word of a two-word type name, as in `::double precision`.
_TYPE_SECOND_WORDS = frozenset(["PRECISION", "VARYING"])

SIMPLE_AGGREGATIONS = {
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


def member_name(member: DimensionSpec | MetricSpec | DerivedMetricSpec) -> str:
    """Return how errors name `member`, as `read_s_layer_blocks` does."""
    if isinstance(member, DimensionSpec):
        kind = "entity" if member.entity else "dimension"
        return f"{kind} {member.name!r} of semantic model {member.table!r}"
    return f"metric {member.name!r}"


def _check_tag_values(spec: SemanticViewSpec) -> None:
    """
    Raise `SLayerBlockError` naming every tag value over Snowflake's limit.

    S_LAYER_KIND is a vocabulary word and always fits; the label and the spec
    are never truncated, so a member whose label or spec is too long stops
    `generate` here rather than `dbt run` later.
    """
    problems: list[str] = []
    for member in (*spec.dimensions, *spec.metrics, *spec.derived_metrics):
        if member.s_layer is None:
            continue
        if member.label is not None and len(member.label) > MAX_TAG_LENGTH:
            problems.append(
                f"{member_name(member)}: S_LAYER_LABEL (the dbt label) is {len(member.label)} "
                f"characters, over the {MAX_TAG_LENGTH}-character tag limit"
            )
        try:
            spec_tag_value(member.s_layer)
        except SpecTooLongError as exc:
            problems.append(f"{member_name(member)}: {exc}")
    if problems:
        raise SLayerBlockError("; ".join(problems), problems=tuple(problems))


def _s_layer(block: SLayerBlock | None, label: str | None) -> str:
    """
    Return a member's S-LAYER slots: `WITH SYNONYMS`, then `WITH TAG`.

    Snowflake accepts them only in that order, before `COMMENT`. Only members with
    an `s_layer` block get them. The tag names are qualified with the
    `s_layer_tags` schema set at the top of the body; S_LAYER_SPEC is left out
    when the block says nothing beyond its kind.
    """
    if block is None:
        return ""
    text = ""
    if block.synonyms:
        names = ", ".join(escape_jinja(sql_string(name)) for name in block.synonyms)
        text += f" WITH SYNONYMS = ({names})"
    tags = [("S_LAYER_KIND", block.kind)]
    if label is not None:
        tags.append(("S_LAYER_LABEL", label))
    spec = to_compact_json(block)
    if spec != "{}":
        tags.append(("S_LAYER_SPEC", spec))
    values = ", ".join(
        f"{{{{ s_layer_tags }}}}.{name} = {escape_jinja(sql_string(value))}" for name, value in tags
    )
    return f"{text} WITH TAG ({values})"


def _qualified(table: str, name: str) -> str:
    return f"{quote_identifier(table)}.{quote_identifier(name)}"


def _expr(expr: str) -> str:
    """
    Return a user expr for the DDL: Jinja escaped, a trailing line comment ended.

    Exprs are pasted inline, so an expr ending in `-- comment` would comment out
    the `,`, `)` or `COMMENT` that follows it on the line. Such an expr gets a
    newline; every other expr is returned as it was.
    """
    text = escape_jinja(expr)
    tokens = scan_sql(expr)
    if tokens and tokens[-1].text.startswith("--"):
        return text + "\n"
    return text


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
    slots = _s_layer(dimension.s_layer, dimension.label)
    return f"  {name} AS {_expr(dimension.expr)}{slots}{_comment(dimension.comment)}"


def _aggregate(view: str, metric: MetricSpec) -> str:
    expr = _expr(metric.expr)
    if metric.agg != "percentile":
        return SIMPLE_AGGREGATIONS[metric.agg].format(expr)
    percentile = metric.percentile
    if percentile is None:
        raise SemanticViewError(
            f"sem__{view}: percentile metric {metric.name!r} has no percentile value."
        )
    # Seven significant digits: Fusion stores `0.9` as the float32 0.8999999761581421.
    value = f"{percentile.value:.7g}"
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
    slots = _s_layer(metric.s_layer, metric.label)
    return f"  {private}{name}{non_additive} AS {aggregate}{slots}{_comment(metric.comment)}"


def _derived(metric: DerivedMetricSpec) -> str:
    expr = "".join(
        _qualified(p.table, p.name) if isinstance(p, MetricRef) else _expr(p) for p in metric.parts
    )
    private = "PRIVATE " if metric.private else ""
    slots = _s_layer(metric.s_layer, metric.label)
    return f"  {private}{quote_identifier(metric.name)} AS {expr}{slots}{_comment(metric.comment)}"


# --- name shadowing (04-RESEARCH, open risk 2) ------------------------------------


def _not_a_column(tokens: list[SqlToken], i: int, calls: list[tuple[str, int]]) -> bool:
    """
    Whether identifier `tokens[i]` is a type name, a date part or another non-column word.

    `tokens` holds the significant tokens of an expr; `calls` the enclosing open
    parens, innermost last, as (upper-case function name or "", argument index).
    """

    def text(j: int) -> str:
        return tokens[j].text.upper() if 0 <= j < len(tokens) else ""

    # `x::date`, `CAST(x AS date)`, `TRY_CAST(x AS timestamp)`.
    if text(i - 1) == "AS" or (text(i - 1) == ":" and text(i - 2) == ":"):
        return True
    # `::double precision`
    if (
        i > 0
        and text(i) in _TYPE_SECOND_WORDS
        and tokens[i - 1].identifier
        and _not_a_column(tokens, i - 1, calls)
    ):
        return True
    # A typed literal (`DATE '2024-01-01'`): no column is followed by a string.
    if text(i + 1).startswith("'"):
        return True
    # `INTERVAL 1 DAY`, `x COLLATE nocase`, `ORDER BY x NULLS FIRST`.
    if text(i - 2) == "INTERVAL" and text(i - 1)[:1].isdigit():
        return True
    if text(i - 1) in ("COLLATE", "NULLS"):
        return True
    # A named argument: `round(x, digits := 2)`.
    if text(i + 1) == ":" and text(i + 2) == "=":
        return True
    if not calls:
        return False
    function, argument = calls[-1]
    if function in _DATE_PART_FIRST:
        return argument == 0 and text(i - 1) == "(" and text(i + 1) == ","
    if function == "EXTRACT":
        return argument == 0 and text(i - 1) == "(" and text(i + 1) == "FROM"
    if function == "LAST_DAY":
        return argument == 1 and text(i - 1) == "," and text(i + 1) == ")"
    return False


def is_significant(token: SqlToken) -> bool:
    """Whether `token` is neither whitespace nor a comment."""
    return bool(token.text.strip()) and not token.text.startswith(("--", "/*"))


def column_reference_indexes(
    tokens: Sequence[SqlToken], table: str, keywords: AbstractSet[str] = _EXPRESSION_KEYWORDS
) -> list[int]:
    """
    Indexes into `tokens` (an expr's `scan_sql`) of the names that may be `table`'s columns.

    Keywords (upper case, in `keywords`), type names (after `::` or a CAST's `AS`),
    date parts (the first argument of `DATE_TRUNC`, `DATEADD`..., `EXTRACT(part FROM
    ...)`, an `INTERVAL`'s unit), typed literals, collations and named arguments are
    not references; a column named like a date part elsewhere in the expr still is.
    A name qualified with another table is not one of `table`'s.
    """
    indexes: list[int] = []
    positions = [j for j, t in enumerate(tokens) if is_significant(t)]
    significant = [tokens[j] for j in positions]
    calls: list[tuple[str, int]] = []
    previous: str | None = None
    for i, token in enumerate(significant):
        if token.text == "(":
            before = significant[i - 1] if i > 0 else None
            name = before.text.upper() if before is not None and before.call else ""
            calls.append((name, 0))
            continue
        if token.text == ")":
            if calls:
                calls.pop()
            continue
        if token.text == "," and calls:
            calls[-1] = (calls[-1][0], calls[-1][1] + 1)
            continue
        if not token.identifier:
            continue
        same_table = not token.qualified or (previous is not None and previous.lower() == table)
        if (
            same_table
            and not token.call
            and token.text.upper() not in keywords
            and not _not_a_column(significant, i, calls)
        ):
            indexes.append(positions[i])
        previous = token.text
    return indexes


def column_references(expr: str, table: str) -> set[str]:
    """Lower-cased names `expr` may resolve against `table`'s own elements."""
    tokens = scan_sql(expr)
    return {tokens[i].text.lower() for i in column_reference_indexes(tokens, table)}


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
            ("metric", m.name, m.expr, SIMPLE_AGGREGATIONS.get(m.agg, "{}").format(m.expr))
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
                if name.lower() in column_references(other_expr, table.alias):
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
    _check_tag_values(spec)
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
    members = (*spec.dimensions, *spec.metrics, *spec.derived_metrics)
    header = CONFIG
    if any(m.s_layer is not None for m in members):
        header += f"\n{{%- set s_layer_tags = {TAG_SCHEMA} %}}"
    body = f"{header}\n\n" + "".join(parts)
    postflight(body, context=f"sem__{spec.name}.sql")
    return body
