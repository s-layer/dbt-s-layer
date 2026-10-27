"""
Render a semantic view as the body of a Databricks `metric_view` model (SEMVW-03).

dbt-databricks (>= 1.12.0) materialises a `metric_view` model by running
`create or replace view <relation> with metrics language yaml as $$ <body> $$`,
so the body is the metric view's YAML definition (06-RESEARCH.md §1, §2). The
root semantic model is the `source`; the tables joined to it are `joins`, nested
by hop. Tables are `{{ ref() }}` calls, so dbt sees the DAG edges.

Field and measure names share one case-insensitive namespace per view, and every
expression is in the source's scope, so a joined table's elements are named after
the entities the joins go through (`order__ordered_at`, as metricflow names them)
and read as `<join path>.<column>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from s_layer_semantic_formats import (
    SpecTooLongError,
    spec_tag_value,
    to_compact_json,
)

from dbt_s_layer.errors import SemanticViewError, SLayerBlockError
from dbt_s_layer.semantic_views import MetricRef, RenderedView, is_bare_identifier
from dbt_s_layer.sentinels import escape_jinja, has_reserved_prefix, jinja_literal, postflight
from dbt_s_layer.snowflake import SIMPLE_AGGREGATIONS, column_references, member_name

if TYPE_CHECKING:
    from s_layer_semantic_formats import SemanticFormat

    from dbt_s_layer.semantic_views import (
        DerivedMetricSpec,
        DimensionSpec,
        MetricSpec,
        RelationshipSpec,
        SemanticViewSpec,
        TableSpec,
    )

# In manifest.json `macros` when dbt-databricks has the materialization (>= 1.12.0).
MATERIALIZATION_MACRO = "macro.dbt_databricks.materialization_metric_view_databricks"
CONFIG = "{{ config(materialized='metric_view', static_analysis='off') }}"
# The Unity Catalog column tags a member with an `s_layer` block gets. Its label is
# the field's `display_name`, so it needs no tag.
TAG_NAMES = ("S_LAYER_KIND", "S_LAYER_SPEC")
# https://docs.databricks.com/aws/en/metric-views/data-modeling/semantic-metadata
MAX_SYNONYMS = 10
MAX_SYNONYM_LENGTH = 255
# https://docs.databricks.com/aws/en/database-objects/tags
MAX_TAG_VALUE_LENGTH = 256
# Comments and display names need spec 1.1, as do nested joins (DBR 17.3+).
SPEC_VERSION = "1.1"
# The name a metric view gives its root table.
SOURCE = "source"

_YAML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    # dbt-databricks wraps the definition in `$$ ... $$`; no `$` may reach it.
    "$": "\\x24",
}
# Not printable in YAML (C1 controls, BOM, non-characters), or a line break YAML 1.1
# folds inside a quoted scalar (NEL, LS, PS).
_YAML_UNSAFE = {0x85, 0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF}


def yaml_string(text: str) -> str:
    """Return `text` as a YAML double-quoted scalar with no `$` or control character in it."""
    out: list[str] = []
    for char in text:
        escaped = _YAML_ESCAPES.get(char)
        code = ord(char)
        if escaped is None and (code < 0x20 or 0x7F <= code <= 0x9F or code in _YAML_UNSAFE):
            escaped = f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"
        out.append(char if escaped is None else escaped)
    return '"' + "".join(out) + '"'


def _text(text: str) -> str:
    """A user-written value: YAML-quoted, then Jinja-escaped so dbt renders it back."""
    return escape_jinja(yaml_string(text))


@dataclass(frozen=True)
class _Place:
    """Where a table sits in the view: how exprs reach it and how its elements are named."""

    path: str
    prefix: str


def _places(spec: SemanticViewSpec) -> dict[str, _Place]:
    root = spec.tables[0].alias
    places = {root: _Place(path=SOURCE, prefix="")}
    for rel in spec.relationships:
        child = places[rel.child]
        path = rel.parent if child.path == SOURCE else f"{child.path}.{rel.parent}"
        places[rel.parent] = _Place(path=path, prefix=f"{child.prefix}{rel.entity}__")
    return places


def _identifier(column: str) -> str:
    # Snowflake allows `$` in a bare name; Databricks SQL needs it quoted.
    return f"`{column}`" if "$" in column else column


def _column(place: _Place, expr: str) -> str:
    return f"{place.path}.{_identifier(expr)}" if is_bare_identifier(expr) else expr


@dataclass(frozen=True)
class _Element:
    kind: str
    name: str
    # The expr as written in the semantic YAML, and as the definition shows it.
    expr: str
    shown: str
    lines: tuple[str, ...]
    # The member it renders, when the member has an `s_layer` block.
    member: DimensionSpec | MetricSpec | DerivedMetricSpec | None = None


def _item(
    name: str,
    expr: str,
    *,
    label: str | None,
    comment: str | None,
    s_layer: SemanticFormat | None = None,
) -> list[str]:
    lines = [f"  - name: {_text(name)}"]
    if label is not None:
        lines.append(f"    display_name: {_text(label)}")
    if comment is not None:
        lines.append(f"    comment: {_text(comment)}")
    if s_layer is not None and s_layer.synonyms:
        lines.append("    synonyms:")
        lines += [f"      - {_text(synonym)}" for synonym in s_layer.synonyms]
    lines.append(f"    expr: {_text(expr)}")
    return lines


def _check_s_layer(elements: list[_Element]) -> None:
    """
    Raise `SLayerBlockError` naming every S-LAYER value Databricks cannot hold.

    Nothing is truncated: a synonym list or tag value over the limit stops
    `generate`, not `dbt run`.
    """
    problems: list[str] = []
    for element in elements:
        member = element.member
        if member is None or member.s_layer is None:
            continue
        where = member_name(member)
        synonyms = member.s_layer.synonyms or ()
        if len(synonyms) > MAX_SYNONYMS:
            problems.append(
                f"{where}: {len(synonyms)} synonyms, over the Databricks limit of {MAX_SYNONYMS}"
            )
        problems += [
            f"{where}: synonym {synonym[:40]!r}... is {len(synonym)} characters, over the "
            f"Databricks limit of {MAX_SYNONYM_LENGTH}"
            for synonym in synonyms
            if len(synonym) > MAX_SYNONYM_LENGTH
        ]
        try:
            spec_tag_value(member.s_layer, MAX_TAG_VALUE_LENGTH)
        except SpecTooLongError as exc:
            problems.append(f"{where}: {exc}")
    if problems:
        raise SLayerBlockError("; ".join(problems), problems=tuple(problems))


def _sql_string(text: str) -> str:
    """A Databricks SQL string literal. Not `''`: Spark reads `'a''b'` as `'a' 'b'`."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _tag_hook(element: _Element) -> str | None:
    """The post-hook that sets an element's column tags, as a Jinja string literal."""
    block = None if element.member is None else element.member.s_layer
    if block is None:
        return None
    tags = [("S_LAYER_KIND", block.kind)]
    spec = to_compact_json(block)
    if spec != "{}":
        tags.append(("S_LAYER_SPEC", spec))
    column = "`" + element.name.replace("`", "``") + "`"
    pairs = ", ".join(f"{_sql_string(name)} = {_sql_string(value)}" for name, value in tags)
    hook = "alter table {{ this }} alter column " + escape_jinja(f"{column} set tags ({pairs})")
    return jinja_literal(hook)


def _config(hooks: list[str]) -> str:
    """
    The model's `config()`: tagged views also re-set their column tags after every run.

    `create or replace` wipes a metric view's column tags, so they are set again
    after each build, and the view is never updated with ALTER VIEW, which could
    keep tags a block no longer has.
    """
    if not hooks:
        return CONFIG
    lines = [
        "{{ config(",
        "    materialized='metric_view',",
        "    static_analysis='off',",
        "    view_update_via_alter=false,",
        "    post_hook=[",
        ",\n".join(f"        {hook}" for hook in hooks),
        "    ]",
        ") }}",
    ]
    return "\n".join(lines)


def _joins(
    spec: SemanticViewSpec, parent: str, places: dict[str, _Place], indent: str
) -> list[str]:
    lines: list[str] = []
    tables = {t.alias: t for t in spec.tables}
    children: list[RelationshipSpec] = [r for r in spec.relationships if r.child == parent]
    if not children:
        return lines
    lines.append(f"{indent}joins:")
    for rel in children:
        table: TableSpec = tables[rel.parent]
        child_name = SOURCE if places[rel.child].path == SOURCE else rel.child
        on = (
            f"{child_name}.{_identifier(rel.child_column)} = "
            f"{rel.parent}.{_identifier(rel.parent_column)}"
        )
        lines.append(f"{indent}  - name: {_text(rel.parent)}")
        lines.append(f"{indent}    source: {yaml_string(table.ref)}")
        lines.append(f'{indent}    "on": {_text(on)}')
        lines += _joins(spec, rel.parent, places, indent + "    ")
    return lines


def _aggregate(view: str, metric: MetricSpec, expr: str) -> str:
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
        if not percentile.discrete:
            raise SemanticViewError(
                f"sem__{view}: metric {metric.name!r} is an approximate continuous percentile, "
                "which Databricks does not support."
            )
        return f"APPROX_PERCENTILE({expr}, {value})"
    function = "PERCENTILE_DISC" if percentile.discrete else "PERCENTILE_CONT"
    return f"{function}({value}) WITHIN GROUP (ORDER BY {expr})"


def _measure(view: str, metric: MetricSpec, place: _Place) -> _Element:
    shown = _aggregate(view, metric, _column(place, metric.expr))
    lines = _item(
        metric.name, shown, label=metric.label, comment=metric.comment, s_layer=metric.s_layer
    )
    if metric.non_additive is not None:
        semiadditive = "last" if metric.non_additive.latest else "first"
        lines += [
            "    window:",
            f"      - order: {_text(metric.non_additive.dimension)}",
            '        range: "current"',
            f'        semiadditive: "{semiadditive}"',
        ]
    return _Element("metric", metric.name, metric.expr, shown, tuple(lines), metric)


def _derived(metric: DerivedMetricSpec) -> _Element:
    shown = "".join(f"MEASURE(`{p.name}`)" if isinstance(p, MetricRef) else p for p in metric.parts)
    lines = _item(
        metric.name, shown, label=metric.label, comment=metric.comment, s_layer=metric.s_layer
    )
    # Only the SQL around the measure references can name a column.
    written = " ".join(p for p in metric.parts if isinstance(p, str))
    return _Element("metric", metric.name, written, shown, tuple(lines), metric)


def _used_inputs(derived: tuple[DerivedMetricSpec, ...]) -> dict[str, str]:
    """Input metric -> the first derived metric (by name) that uses it."""
    used: dict[str, str] = {}
    for metric in sorted(derived, key=lambda d: d.name):
        for part in metric.parts:
            if isinstance(part, MetricRef):
                used.setdefault(part.name, metric.name)
    return used


def _check_names(view: str, elements: list[_Element]) -> None:
    seen: dict[str, str] = {}
    for element in elements:
        if has_reserved_prefix(element.name):
            raise SemanticViewError(
                f"sem__{view}: {element.kind} name {element.name!r} contains a prefix "
                "dbt-s-layer reserves for its table placeholders. Rename the entity or "
                "semantic model it is built from."
            )
        folded = element.name.lower()
        if folded in seen:
            raise SemanticViewError(
                f"sem__{view}: {seen[folded]!r} and {element.name!r} both become {folded!r}; "
                "a metric view's field and measure names are one case-insensitive namespace. "
                "Rename one of them."
            )
        seen[folded] = element.name


def _check_shadowing(view: str, elements: list[_Element]) -> None:
    """
    Refuse an element whose name would capture a column another element's expr uses.

    Metric view expressions can use earlier fields by name, so a field `amount`
    defined as `amount * 100` changes a measure `SUM(amount - tax)` (seen on a
    workspace: the dimension wins). A plain column is written `source.<col>` and is
    never captured. A dimension expr reaches measures only through `MEASURE()`, so a
    measure's name cannot capture a column there.
    """
    for element in elements:
        if element.kind == "dimension" and element.expr.strip().lower() == element.name.lower():
            continue
        for other in elements:
            if other is element or is_bare_identifier(other.expr):
                continue
            if element.kind == "metric" and other.kind == "dimension":
                continue
            if element.name.lower() in column_references(other.expr, ""):
                raise SemanticViewError(
                    f"sem__{view}: {element.kind} {element.name!r} ({element.expr!r}) has the "
                    f"name of a column that {other.kind} {other.name!r} uses ({other.shown!r}). "
                    f"The metric view could resolve the name to the {element.kind}, not the "
                    f"column, which would change {other.name}. Rename the {element.kind}, or "
                    f"make its expr exactly {element.name!r}."
                )


def render_databricks(spec: SemanticViewSpec) -> RenderedView:
    """Return the model body: `{{ config() }}`, a blank line, and the metric view YAML."""
    view = spec.name
    root = spec.tables[0]
    for table in spec.tables[1:]:
        if table.alias.lower() == SOURCE:
            raise SemanticViewError(
                f"sem__{view}: semantic model {table.alias!r} cannot be joined in a metric view, "
                f"where {SOURCE!r} names the root table. Rename the semantic model."
            )
    places = _places(spec)
    warnings: list[str] = []
    joined_through = {r.parent: r.entity for r in spec.relationships}

    dimensions: list[_Element] = []
    for dimension in spec.dimensions:
        place = places[dimension.table]
        if dimension.table != root.alias:
            # The join's own key is the child's entity of the same name.
            if dimension.name == joined_through.get(dimension.table):
                continue
            if not is_bare_identifier(dimension.expr):
                warnings.append(
                    f"semantic views: sem__{view}: skipped dimension "
                    f"{place.prefix + dimension.name!r} of semantic model {dimension.table!r}: "
                    "on Databricks a joined table's dimension must be a plain column "
                    f"({dimension.expr!r})."
                )
                continue
        name = place.prefix + dimension.name
        shown = _column(place, dimension.expr)
        lines = _item(
            name,
            shown,
            label=dimension.label,
            comment=dimension.comment,
            s_layer=dimension.s_layer,
        )
        dimensions.append(
            _Element("dimension", name, dimension.expr, shown, tuple(lines), dimension)
        )

    # A metric view has no hidden measures: keep a private metric only when a
    # derived metric needs it.
    derived = tuple(d for d in spec.derived_metrics if not d.private)
    used = _used_inputs(derived)
    metrics: list[MetricSpec] = []
    for metric in sorted(spec.metrics, key=lambda m: m.name):
        if metric.private:
            if metric.name not in used:
                continue
            warnings.append(
                f"semantic views: sem__{view}: private metric {metric.name!r} is visible in the "
                f"Databricks metric view, because {used[metric.name]!r} uses it (metric views "
                "have no private measures)."
            )
        metrics.append(metric)
    measures = [_measure(view, m, places[m.table]) for m in metrics]
    measures += [_derived(d) for d in sorted(derived, key=lambda d: d.name)]

    if not dimensions and not measures:
        raise SemanticViewError(
            f"sem__{view}: a metric view needs at least one dimension or metric."
        )
    _check_names(view, dimensions + measures)
    _check_shadowing(view, dimensions + measures)
    _check_s_layer(dimensions + measures)
    hooks = [hook for e in dimensions + measures if (hook := _tag_hook(e)) is not None]

    lines = [f"version: {SPEC_VERSION}"]
    if spec.comment is not None:
        lines.append(f"comment: {_text(spec.comment)}")
    lines.append(f"source: {yaml_string(root.ref)}")
    lines += _joins(spec, root.alias, places, "")
    if dimensions:
        lines.append("dimensions:")
        lines += [line for d in dimensions for line in d.lines]
    if measures:
        lines.append("measures:")
        lines += [line for m in measures for line in m.lines]
    body = f"{_config(hooks)}\n\n" + "\n".join(lines) + "\n"
    postflight(body, context=f"sem__{view}.sql")
    return RenderedView(body=body, warnings=tuple(sorted(warnings)))
