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

from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.semantic_views import MetricRef, RenderedView, is_bare_identifier
from dbt_s_layer.sentinels import escape_jinja, postflight
from dbt_s_layer.snowflake import column_references

if TYPE_CHECKING:
    from dbt_s_layer.semantic_views import (
        DerivedMetricSpec,
        MetricSpec,
        RelationshipSpec,
        SemanticViewSpec,
        TableSpec,
    )

# In manifest.json `macros` when dbt-databricks has the materialization (>= 1.12.0).
MATERIALIZATION_MACRO = "macro.dbt_databricks.materialization_metric_view_databricks"
CONFIG = "{{ config(materialized='metric_view', static_analysis='off') }}"
# Comments and display names need spec 1.1, as do nested joins (DBR 17.3+).
SPEC_VERSION = "1.1"
# The name a metric view gives its root table.
SOURCE = "source"

_SIMPLE_AGGREGATIONS = {
    "sum": "SUM({})",
    "min": "MIN({})",
    "max": "MAX({})",
    "average": "AVG({})",
    "count": "COUNT({})",
    "count_distinct": "COUNT(DISTINCT {})",
    "sum_boolean": "SUM(CASE WHEN {} THEN 1 ELSE 0 END)",
}

_YAML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    # dbt-databricks wraps the definition in `$$ ... $$`; no `$` may reach it.
    "$": "\\x24",
}


def yaml_string(text: str) -> str:
    """Return `text` as a YAML double-quoted scalar with no `$` or control character in it."""
    out: list[str] = []
    for char in text:
        escaped = _YAML_ESCAPES.get(char)
        if escaped is None and (ord(char) < 0x20 or ord(char) == 0x7F):
            escaped = f"\\x{ord(char):02x}"
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


def _column(place: _Place, expr: str) -> str:
    return f"{place.path}.{expr}" if is_bare_identifier(expr) else expr


@dataclass(frozen=True)
class _Element:
    kind: str
    name: str
    # The expr as written in the semantic YAML, and as the definition shows it.
    expr: str
    shown: str
    lines: tuple[str, ...]


def _item(name: str, expr: str, *, label: str | None, comment: str | None) -> list[str]:
    lines = [f"  - name: {_text(name)}"]
    if label is not None:
        lines.append(f"    display_name: {_text(label)}")
    if comment is not None:
        lines.append(f"    comment: {_text(comment)}")
    lines.append(f"    expr: {_text(expr)}")
    return lines


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
        on = f"{child_name}.{rel.child_column} = {rel.parent}.{rel.parent_column}"
        lines.append(f"{indent}  - name: {_text(rel.parent)}")
        lines.append(f"{indent}    source: {yaml_string(table.ref)}")
        lines.append(f'{indent}    "on": {_text(on)}')
        lines += _joins(spec, rel.parent, places, indent + "    ")
    return lines


def _aggregate(view: str, metric: MetricSpec, expr: str) -> str:
    if metric.agg != "percentile":
        return _SIMPLE_AGGREGATIONS[metric.agg].format(expr)
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
    lines = _item(metric.name, shown, label=metric.label, comment=metric.comment)
    if metric.non_additive is not None:
        semiadditive = "last" if metric.non_additive.latest else "first"
        lines += [
            "    window:",
            f"      - order: {_text(metric.non_additive.dimension)}",
            '        range: "current"',
            f'        semiadditive: "{semiadditive}"',
        ]
    return _Element("metric", metric.name, metric.expr, shown, tuple(lines))


def _derived(metric: DerivedMetricSpec) -> _Element:
    shown = "".join(f"MEASURE(`{p.name}`)" if isinstance(p, MetricRef) else p for p in metric.parts)
    lines = _item(metric.name, shown, label=metric.label, comment=metric.comment)
    # Only the SQL around the measure references can name a column.
    written = " ".join(p for p in metric.parts if isinstance(p, str))
    return _Element("metric", metric.name, written, shown, tuple(lines))


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
    defined as `amount * 100` could change a measure `SUM(amount - tax)`. A plain
    column is written `source.<col>` and is never captured.
    """
    for element in elements:
        if element.kind == "dimension" and element.expr.strip().lower() == element.name.lower():
            continue
        for other in elements:
            if other is element or is_bare_identifier(other.expr):
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
    if not spec.dimensions and not spec.metrics and not spec.derived_metrics:
        raise SemanticViewError(
            f"sem__{view}: a metric view needs at least one dimension or metric."
        )
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
        lines = _item(name, shown, label=dimension.label, comment=dimension.comment)
        dimensions.append(_Element("dimension", name, dimension.expr, shown, tuple(lines)))

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

    _check_names(view, dimensions + measures)
    _check_shadowing(view, dimensions + measures)

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
    body = f"{CONFIG}\n\n" + "\n".join(lines) + "\n"
    postflight(body, context=f"sem__{view}.sql")
    return RenderedView(body=body, warnings=tuple(sorted(warnings)))
