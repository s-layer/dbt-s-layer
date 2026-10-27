"""
Render a semantic view as the body of a DuckDB `semantic_view` model (SEMVW-04).

The `duckdb_semantic_views` dbt package (s-layer/dbt-duckdb-semantic-views)
materialises a model by running `create or replace semantic view <relation> as`
followed by the model's SQL, so the body is the clause list of the
`semantic_views` extension: TABLES, RELATIONSHIPS, DIMENSIONS, METRICS and
COMMENT, as on Snowflake (07-RESEARCH.md §1). Tables are `{{ ref() }}` calls, so
dbt sees the DAG edges.

Three things differ from Snowflake. Dimension and metric names share one
case-insensitive namespace per view, so a joined table's members are named after
the entities the joins go through (`order__status`), as on Databricks. A bare
column binds against every joined table, so in a multi-table view every column
an expr reads is qualified with its table; an expr whose columns cannot be told
apart (a lambda, a subquery) leaves its member out with a warning. And a quoted
table alias breaks every query, so aliases are written bare, with `_t` appended
to a semantic model name DuckDB reserves.

The body is written so that extension 0.11 (the one dbt Fusion's DuckDB loads)
accepts it as well as 0.13 and later: derived metrics name their inputs bare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.semantic_views import MetricRef, RenderedView, is_bare_identifier, scan_sql
from dbt_s_layer.sentinels import escape_jinja, has_reserved_prefix, postflight
from dbt_s_layer.snowflake import (
    SIMPLE_AGGREGATIONS,
    check_tag_values,
    column_reference_indexes,
    is_significant,
    s_layer_tag_values,
)

if TYPE_CHECKING:
    from s_layer_semantic_formats import SemanticFormat

    from dbt_s_layer.semantic_views import (
        DerivedMetricSpec,
        DimensionSpec,
        MetricSpec,
        SemanticViewSpec,
    )

PACKAGE = "duckdb_semantic_views"
REPOSITORY = "s-layer/dbt-duckdb-semantic-views"
# In manifest.json `macros` when the package is installed.
MATERIALIZATION_MACRO = "macro.duckdb_semantic_views.materialization_semantic_view_duckdb"
CONFIG = "{{ config(materialized='semantic_view', static_analysis='off') }}"
# Sets `s_layer_tags`: whether members with an `s_layer` block get `WITH TAG`. The
# clause needs extension 0.17; an older one keeps it as part of the expression. Unless
# the `s_layer_duckdb_tags` var says, the loaded extension's version decides (a local
# build, whose version is a commit hash, gets none); `dbt parse` runs no query.
_TAGGING_EXTENSION = (
    "select count(*) from duckdb_extensions() where extension_name = 'semantic_views' "
    "and loaded and regexp_matches(extension_version, '^v(0[.](1[7-9]|[2-9][0-9])|[1-9][0-9]*)[.]')"
)
TAGS_SWITCH = (
    "{%- set s_layer_tags = var('s_layer_duckdb_tags', none) %}\n"
    "{%- if s_layer_tags is none %}\n"
    "{%- set s_layer_tags = execute and run_query(\n"
    f'    "{_TAGGING_EXTENSION}"\n'
    ").columns[0].values()[0] > 0 %}\n"
    "{%- endif %}"
)
# What a table alias gets when its semantic model's name is a reserved word.
ALIAS_SUFFIX = "_t"

# `select keyword_name from duckdb_keywords() where keyword_category = 'reserved'`,
# the same on DuckDB 1.5.4, 1.5.6 and 2.0.0 (2026-10-26).
RESERVED_WORDS = frozenset(
    [
        "ALL",
        "ANALYSE",
        "ANALYZE",
        "AND",
        "ANY",
        "ARRAY",
        "AS",
        "ASC",
        "ASYMMETRIC",
        "BOTH",
        "CASE",
        "CAST",
        "CHECK",
        "COLLATE",
        "COLUMN",
        "CONSTRAINT",
        "CREATE",
        "DEFAULT",
        "DEFERRABLE",
        "DESC",
        "DESCRIBE",
        "DISTINCT",
        "DO",
        "ELSE",
        "END",
        "EXCEPT",
        "FALSE",
        "FETCH",
        "FOR",
        "FOREIGN",
        "FROM",
        "GROUP",
        "HAVING",
        "IN",
        "INITIALLY",
        "INTERSECT",
        "INTO",
        "LAMBDA",
        "LATERAL",
        "LEADING",
        "LIMIT",
        "NOT",
        "NULL",
        "OFFSET",
        "ON",
        "ONLY",
        "OR",
        "ORDER",
        "PIVOT",
        "PIVOT_LONGER",
        "PIVOT_WIDER",
        "PLACING",
        "PRIMARY",
        "QUALIFY",
        "REFERENCES",
        "RETURNING",
        "SELECT",
        "SHOW",
        "SOME",
        "SUMMARIZE",
        "SYMMETRIC",
        "TABLE",
        "THEN",
        "TO",
        "TRAILING",
        "TRUE",
        "UNION",
        "UNIQUE",
        "UNPIVOT",
        "USING",
        "VARIADIC",
        "WHEN",
        "WHERE",
        "WINDOW",
        "WITH",
    ]
)
# Words inside exprs that are not columns although DuckDB does not reserve them.
_EXPRESSION_KEYWORDS = RESERVED_WORDS | {
    "BETWEEN",
    "CURRENT",
    "CURRENT_DATE",
    "CURRENT_TIME",
    "CURRENT_TIMESTAMP",
    "ESCAPE",
    "EXISTS",
    "FILTER",
    "FOLLOWING",
    "GLOB",
    "GROUPS",
    "ILIKE",
    "INTERVAL",
    "IS",
    "ISNULL",
    "LIKE",
    "LOCALTIME",
    "LOCALTIMESTAMP",
    "NOTNULL",
    "NULLS",
    "OVER",
    "PARTITION",
    "PRECEDING",
    "RANGE",
    "ROW",
    "ROWS",
    "SIMILAR",
    "UNBOUNDED",
    "WITHIN",
}
# Member names written bare; `as` would end the name (`orders.as AS ...`).
_PLAIN_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*")
_PLAIN_COLUMN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def quote_name(name: str) -> str:
    """Return a member or relationship name, bare unless DuckDB needs it quoted."""
    if _PLAIN_NAME_RE.fullmatch(name) and name != "as":
        return name
    return '"' + name.replace('"', '""') + '"'


def quote_column(column: str) -> str:
    """Return a column name, bare unless it is reserved or not a plain identifier."""
    if _PLAIN_COLUMN_RE.fullmatch(column) and column.upper() not in RESERVED_WORDS:
        return column
    return '"' + column.replace('"', '""') + '"'


def sql_string(text: str) -> str:
    """A DuckDB string literal for `text`, Jinja-escaped. Backslashes and newlines are literal."""
    return escape_jinja("'" + text.replace("'", "''") + "'")


def _comment(text: str | None) -> str:
    return "" if text is None else f" COMMENT = {sql_string(text)}"


def _s_layer(block: SemanticFormat | None, label: str | None) -> str:
    """
    Return a member's S-LAYER slots: `WITH SYNONYMS`, then `WITH TAG` behind the switch.

    Only members with an `s_layer` block get them. The tag names are bare: DuckDB
    stores them as written, with no tag objects to create.
    """
    if block is None:
        return ""
    text = ""
    if block.synonyms:
        text += f" WITH SYNONYMS = ({', '.join(sql_string(s) for s in block.synonyms)})"
    values = ", ".join(
        f"{name} = {sql_string(value)}" for name, value in s_layer_tag_values(block, label)
    )
    return f"{text}{{% if s_layer_tags %}} WITH TAG ({values}){{% endif %}}"


def _lambda_or_subquery(words: list[str]) -> bool:
    """Whether an expr's significant tokens hold a lambda, a subquery or a list comprehension."""
    opened: list[str] = []
    for i, word in enumerate(words):
        if word in ("(", "["):
            opened.append(word)
        elif word in (")", "]") and opened:
            opened.pop()
        # `[x * 2 FOR x IN xs]` is a list comprehension (`substring(s FROM 1 FOR 3)` is
        # not); `(FROM t)` is a FROM-first subquery.
        if word in ("LAMBDA", "SELECT") or (word == "FOR" and opened[-1:] == ["["]):
            return True
        if word == "FROM" and words[i - 1 : i] == ["("]:
            return True
        # `x -> x + 1` is a lambda; `j -> 'key'`, `j ->> '$.key'` and `j -> 0` read JSON.
        if word == "-" and words[i + 1 : i + 2] == [">"]:
            after = words[i + 2] if i + 2 < len(words) else ""
            if not (after == ">" or after.startswith("'") or after[:1].isdigit()):
                return True
    return False


def qualify(expr: str, alias: str) -> str | None:
    """
    Return `expr` with each of its column references qualified with `alias`.

    Returns None when the expr holds a lambda, a subquery or a list comprehension,
    whose names cannot be told apart from the table's columns. Names already
    qualified, keywords, type names, date parts, function names and named arguments
    are left as written.
    """
    tokens = scan_sql(expr)
    positions = [i for i, t in enumerate(tokens) if is_significant(t)]
    words = [tokens[i].text.upper() for i in positions]
    if _lambda_or_subquery(words):
        return None

    def word(k: int) -> str:
        return words[k] if 0 <= k < len(words) else ""

    candidates = set(column_reference_indexes(tokens, "", _EXPRESSION_KEYWORDS))
    columns: set[int] = set()
    for k, i in enumerate(positions):
        quoted = tokens[i].text.startswith('"')
        if i not in candidates and not quoted:
            continue
        # A qualifier (`orders.amount`, `s.field`), or a quoted name used as one.
        if word(k + 1) == "." or (quoted and word(k - 1) == "."):
            continue
        if quoted and (word(k - 1) in ("AS", ":") or word(k + 1) == "("):
            continue
        columns.add(i)
    return "".join(f"{alias}.{t.text}" if i in columns else t.text for i, t in enumerate(tokens))


def _expr(expr: str) -> str:
    """
    Return an expr for the DDL: Jinja escaped, a trailing line comment ended.

    An expr ending in `-- comment` would comment out the `,` or `)` after it, so
    such an expr gets a newline.
    """
    text = escape_jinja(expr)
    tokens = scan_sql(expr)
    if tokens and tokens[-1].text.startswith("--"):
        return text + "\n"
    return text


@dataclass(frozen=True)
class _Place:
    """How a table is written in the view, and how its members are named."""

    alias: str
    prefix: str


def _aliases(spec: SemanticViewSpec) -> dict[str, str]:
    """Semantic model -> its table alias: the name, or `<name>_t` when DuckDB reserves it."""
    aliases: dict[str, str] = {}
    for table in spec.tables:
        name = table.alias
        alias = name + ALIAS_SUFFIX if name.upper() in RESERVED_WORDS else name
        if alias != quote_name(alias):
            raise SemanticViewError(
                f"sem__{spec.name}: semantic model {name!r} cannot be a DuckDB table alias; "
                "rename it to a lower-case name of letters, digits and underscores."
            )
        aliases[name] = alias
    taken: dict[str, str] = {}
    for name, alias in aliases.items():
        if alias in taken:
            raise SemanticViewError(
                f"sem__{spec.name}: semantic models {taken[alias]!r} and {name!r} would both "
                f"be table {alias!r} (DuckDB reserves {taken[alias]!r}, so its alias gets "
                f"{ALIAS_SUFFIX!r}). Rename one of them."
            )
        taken[alias] = name
    return aliases


def _places(spec: SemanticViewSpec, aliases: dict[str, str]) -> dict[str, _Place]:
    root = spec.tables[0].alias
    places = {root: _Place(alias=aliases[root], prefix="")}
    for rel in spec.relationships:
        child = places[rel.child]
        places[rel.parent] = _Place(
            alias=aliases[rel.parent], prefix=f"{child.prefix}{rel.entity}__"
        )
    return places


@dataclass(frozen=True)
class _Member:
    kind: str
    # The name in the view, unquoted.
    name: str
    line: str
    spec: DimensionSpec | MetricSpec | DerivedMetricSpec


class _Renderer:
    def __init__(self, spec: SemanticViewSpec) -> None:
        self.spec = spec
        self.view = spec.name
        self.aliases = _aliases(spec)
        self.places = _places(spec, self.aliases)
        # One table cannot be ambiguous: its exprs are kept as written.
        self.qualifying = len(spec.tables) > 1
        self.warnings: list[str] = []

    def _skip(self, kind: str, name: str, why: str) -> None:
        self.warnings.append(f"semantic views: sem__{self.view}: skipped {kind} {name!r}{why}")

    def expr(self, kind: str, name: str, table: str, expr: str) -> str | None:
        """The expr in the table's scope, or None (with a warning) when it cannot be read."""
        alias = self.places[table].alias
        if is_bare_identifier(expr):
            return f"{alias}.{quote_column(expr)}"
        if not self.qualifying:
            return _expr(expr)
        qualified = qualify(expr, alias)
        if qualified is None:
            self._skip(
                kind,
                name,
                f" of semantic model {table!r}: dbt-s-layer cannot tell which names in its "
                f"expr are columns ({expr!r}), and DuckDB needs every column of a multi-table "
                "semantic view qualified with its table.",
            )
            return None
        return _expr(qualified)

    def table(self, name: str) -> str:
        table = next(t for t in self.spec.tables if t.alias == name)
        lines = [f"  {self.aliases[name]} AS {table.ref}"]
        if table.primary_key is not None:
            lines.append(f"    PRIMARY KEY ({quote_column(table.primary_key)})")
        lines += [f"    UNIQUE ({quote_column(column)})" for column in table.unique_keys]
        if table.comment is not None:
            lines.append(f"   {_comment(table.comment)}")
        return "\n".join(lines)

    def dimensions(self) -> list[_Member]:
        root = self.spec.tables[0].alias
        joined_through = {r.parent: r.entity for r in self.spec.relationships}
        members: list[_Member] = []
        for dimension in self.spec.dimensions:
            # A joined table's key is the entity of the same name on the table it joins from.
            if dimension.table != root and dimension.name == joined_through.get(dimension.table):
                continue
            place = self.places[dimension.table]
            name = place.prefix + dimension.name
            expr = self.expr("dimension", name, dimension.table, dimension.expr)
            if expr is None:
                continue
            line = (
                f"  {place.alias}.{quote_name(name)} AS {expr}"
                f"{_comment(dimension.comment)}{_s_layer(dimension.s_layer, dimension.label)}"
            )
            members.append(_Member("dimension", name, line, dimension))
        return members

    def aggregate(self, metric: MetricSpec, expr: str) -> str:
        if metric.agg != "percentile":
            return SIMPLE_AGGREGATIONS[metric.agg].format(expr)
        percentile = metric.percentile
        if percentile is None:
            raise SemanticViewError(
                f"sem__{self.view}: percentile metric {metric.name!r} has no percentile value."
            )
        # Seven significant digits: Fusion stores `0.9` as the float32 0.8999999761581421.
        value = f"{percentile.value:.7g}"
        if percentile.approximate:
            if percentile.discrete:
                raise SemanticViewError(
                    f"sem__{self.view}: metric {metric.name!r} is an approximate discrete "
                    "percentile, which DuckDB does not support."
                )
            # metricflow's DuckDB renderer uses the same function.
            return f"APPROX_QUANTILE({expr}, {value})"
        function = "PERCENTILE_DISC" if percentile.discrete else "PERCENTILE_CONT"
        return f"{function}({value}) WITHIN GROUP (ORDER BY {expr})"

    def metrics(self, dimensions: set[str]) -> list[_Member]:
        members: list[_Member] = []
        for metric in self.spec.metrics:
            place = self.places[metric.table]
            expr = self.expr("metric", metric.name, metric.table, metric.expr)
            if expr is None:
                continue
            non_additive = ""
            if metric.non_additive is not None:
                dimension = place.prefix + metric.non_additive.dimension
                if dimension not in dimensions:
                    self._skip(
                        "metric",
                        metric.name,
                        f": its non-additive dimension {dimension!r} was skipped.",
                    )
                    continue
                order = "ASC" if metric.non_additive.latest else "DESC"
                non_additive = f" NON ADDITIVE BY ({place.alias}.{quote_name(dimension)} {order})"
            private = "PRIVATE " if metric.private else ""
            line = (
                f"  {private}{place.alias}.{quote_name(metric.name)}{non_additive} AS "
                f"{self.aggregate(metric, expr)}{_comment(metric.comment)}"
                f"{_s_layer(metric.s_layer, metric.label)}"
            )
            members.append(_Member("metric", metric.name, line, metric))
        return members

    def derived(self, metric: DerivedMetricSpec, available: set[str]) -> _Member | None:
        for part in metric.parts:
            if isinstance(part, MetricRef) and part.name not in available:
                self._skip("metric", metric.name, f": its input {part.name!r} was skipped.")
                return None
        # Bare input names: extension 0.11 cannot bind `orders.revenue` here.
        expr = "".join(
            quote_name(p.name) if isinstance(p, MetricRef) else _expr(p) for p in metric.parts
        )
        private = "PRIVATE " if metric.private else ""
        line = (
            f"  {private}{quote_name(metric.name)} AS {expr}"
            f"{_comment(metric.comment)}{_s_layer(metric.s_layer, metric.label)}"
        )
        return _Member("metric", metric.name, line, metric)

    def check_names(self, members: list[_Member]) -> None:
        seen: dict[str, str] = {}
        for member in members:
            if has_reserved_prefix(member.name):
                raise SemanticViewError(
                    f"sem__{self.view}: {member.kind} name {member.name!r} contains a prefix "
                    "dbt-s-layer reserves for its table placeholders. Rename the entity or "
                    "semantic model it is built from."
                )
            folded = member.name.lower()
            if folded in seen:
                raise SemanticViewError(
                    f"sem__{self.view}: {seen[folded]!r} and {member.name!r} both become "
                    f"{folded!r}; a DuckDB semantic view's dimension and metric names are one "
                    "case-insensitive namespace. Rename one of them."
                )
            seen[folded] = member.name


def _clause(keyword: str, items: list[str]) -> str:
    return f"{keyword} (\n" + ",\n".join(items) + "\n)\n"


def render_duckdb(spec: SemanticViewSpec) -> RenderedView:
    """Return the model body: `{{ config() }}`, a blank line, and the semantic view clauses."""
    renderer = _Renderer(spec)
    dimensions = renderer.dimensions()
    metrics = renderer.metrics({d.name for d in dimensions})
    available = {m.name for m in metrics}
    for metric in spec.derived_metrics:
        member = renderer.derived(metric, available)
        if member is not None:
            metrics.append(member)
    if not dimensions and not metrics:
        raise SemanticViewError(
            f"sem__{spec.name}: a semantic view needs at least one dimension or metric."
        )
    renderer.check_names(dimensions + metrics)
    # Only members that are written: a left-out member's tags never reach DuckDB.
    check_tag_values(m.spec for m in dimensions + metrics)

    parts = [_clause("TABLES", [renderer.table(t.alias) for t in spec.tables])]
    if spec.relationships:
        aliases = renderer.aliases
        parts.append(
            _clause(
                "RELATIONSHIPS",
                [
                    f"  {quote_name(r.name)} AS {aliases[r.child]} ({quote_column(r.child_column)})"
                    f" REFERENCES {aliases[r.parent]} ({quote_column(r.parent_column)})"
                    for r in spec.relationships
                ],
            )
        )
    if dimensions:
        parts.append(_clause("DIMENSIONS", [d.line for d in dimensions]))
    if metrics:
        parts.append(_clause("METRICS", [m.line for m in metrics]))
    if spec.comment is not None:
        parts.append(f"COMMENT = {sql_string(spec.comment)}\n")
    header = CONFIG
    if any(m.spec.s_layer is not None for m in dimensions + metrics):
        header += "\n" + TAGS_SWITCH
    body = f"{header}\n\n" + "".join(parts)
    postflight(body, context=f"sem__{spec.name}.sql")
    return RenderedView(body=body, warnings=tuple(sorted(renderer.warnings)))
