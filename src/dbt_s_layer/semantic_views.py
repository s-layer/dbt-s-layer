"""
Describe one semantic view per semantic model, independent of any warehouse (SEMVW-01, SEMVW-05).

A view holds its root semantic model plus the semantic models metricflow could
join to it (its join closure), with the root's metrics. Adapters render a
`SemanticViewSpec` into their own DDL: Snowflake in `snowflake.py`, Databricks
and DuckDB in later phases. Features a semantic view cannot express are left
out with one warning each, never an error, so a project that uses them still
generates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from metricflow_semantic_interfaces.type_enums.aggregation_type import AggregationType
from metricflow_semantic_interfaces.type_enums.entity_type import EntityType
from metricflow_semantic_interfaces.type_enums.metric_type import MetricType

from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.refs import build_ref_map
from dbt_s_layer.sentinels import render_ref

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from metricflow_semantic_interfaces.implementations.metric import (
        PydanticMetric,
        PydanticMetricInput,
    )
    from metricflow_semantic_interfaces.implementations.semantic_model import (
        PydanticSemanticModel,
    )

    from dbt_s_layer.project import SemanticProject

# metricflow's MAX_JOIN_HOPS.
MAX_JOIN_HOPS = 2

Aggregation = Literal[
    "sum", "min", "max", "average", "count", "count_distinct", "sum_boolean", "percentile"
]

_AGGREGATIONS: dict[AggregationType, Aggregation] = {
    AggregationType.SUM: "sum",
    AggregationType.MIN: "min",
    AggregationType.MAX: "max",
    AggregationType.AVERAGE: "average",
    AggregationType.COUNT: "count",
    AggregationType.COUNT_DISTINCT: "count_distinct",
    AggregationType.SUM_BOOLEAN: "sum_boolean",
    AggregationType.PERCENTILE: "percentile",
    AggregationType.MEDIAN: "percentile",
}
# Entities that can stand on the "one" side of a join.
_KEY_TYPES = (EntityType.PRIMARY, EntityType.UNIQUE)
# Entities a join can start from (natural entities are left out).
_JOIN_TYPES = (EntityType.PRIMARY, EntityType.UNIQUE, EntityType.FOREIGN)

_BARE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_NUMBER_RE = re.compile(r"[0-9][A-Za-z0-9_.$]*")


# --- the IR -------------------------------------------------------------------


@dataclass(frozen=True)
class TableSpec:
    """A logical table: one semantic model and the dbt node it reads."""

    alias: str
    semantic_model: str
    # `{{ ref(...) }}` for the node the semantic model reads.
    ref: str
    primary_key: str | None = None
    unique_keys: tuple[str, ...] = ()
    comment: str | None = None


@dataclass(frozen=True)
class RelationshipSpec:
    """A many-to-one (or one-to-one) join from `child` to `parent`."""

    name: str
    child: str
    child_column: str
    parent: str
    parent_column: str


@dataclass(frozen=True)
class DimensionSpec:
    """A dimension of one table: a categorical or time dimension, or an entity."""

    table: str
    name: str
    expr: str
    comment: str | None = None


@dataclass(frozen=True)
class PercentileSpec:
    """A percentile aggregation's parameters."""

    value: float
    discrete: bool
    approximate: bool


@dataclass(frozen=True)
class NonAdditiveSpec:
    """Aggregate only the latest (or earliest) rows by `dimension` of the metric's table."""

    dimension: str
    latest: bool


@dataclass(frozen=True)
class MetricSpec:
    """An aggregation over one table: a metricflow simple metric."""

    table: str
    name: str
    agg: Aggregation
    expr: str
    percentile: PercentileSpec | None = None
    non_additive: NonAdditiveSpec | None = None
    private: bool = False
    comment: str | None = None


@dataclass(frozen=True)
class MetricRef:
    """A reference to a metric of a table, inside a derived metric."""

    table: str
    name: str


@dataclass(frozen=True)
class DerivedMetricSpec:
    """A view-level metric over other metrics: SQL text with metric references in it."""

    name: str
    parts: tuple[str | MetricRef, ...]
    private: bool = False
    comment: str | None = None


@dataclass(frozen=True)
class SemanticViewSpec:
    """Everything one semantic view holds; the root table comes first."""

    name: str
    tables: tuple[TableSpec, ...]
    relationships: tuple[RelationshipSpec, ...] = ()
    dimensions: tuple[DimensionSpec, ...] = ()
    metrics: tuple[MetricSpec, ...] = ()
    derived_metrics: tuple[DerivedMetricSpec, ...] = ()
    comment: str | None = None


@dataclass(frozen=True)
class SemanticViewBuild:
    """The views of a project, sorted by name, and what was left out of them."""

    views: tuple[SemanticViewSpec, ...]
    warnings: tuple[str, ...]


# --- SQL scanning ---------------------------------------------------------------


@dataclass(frozen=True)
class SqlToken:
    """A piece of a SQL expression. Joining every token's text gives the expression back."""

    text: str
    # A bare identifier: not in a string literal, a quoted identifier or a comment.
    identifier: bool = False
    # Preceded by `.`, as in `orders.amount`.
    qualified: bool = False
    # Followed by `(`: a function name.
    call: bool = False


def _skip_quoted(sql: str, start: int, quote: str) -> int:
    """Return the index after the literal or quoted identifier starting at `start`."""
    i = start + 1
    while i < len(sql):
        char = sql[i]
        if quote == "'" and char == "\\":
            i += 2
            continue
        if char == quote:
            if sql.startswith(quote, i + 1):
                i += 2
                continue
            return i + 1
        i += 1
    return len(sql)


def scan_sql(sql: str) -> tuple[SqlToken, ...]:
    """Split a SQL expression into identifiers and everything else."""
    tokens: list[SqlToken] = []
    i = 0
    while i < len(sql):
        char = sql[i]
        if char in "'\"":
            end = _skip_quoted(sql, i, char)
        elif sql.startswith("--", i):
            newline = sql.find("\n", i)
            end = len(sql) if newline < 0 else newline
        elif sql.startswith("/*", i):
            close = sql.find("*/", i + 2)
            end = len(sql) if close < 0 else close + 2
        elif (number := _NUMBER_RE.match(sql, i)) is not None:
            end = number.end()
        elif (ident := _BARE_IDENTIFIER_RE.match(sql, i)) is not None:
            end = ident.end()
            tokens.append(
                SqlToken(
                    sql[i:end],
                    identifier=True,
                    qualified=sql[:i].rstrip().endswith("."),
                    call=sql[end:].lstrip().startswith("("),
                )
            )
            i = end
            continue
        else:
            end = i + 1
        tokens.append(SqlToken(sql[i:end]))
        i = end
    return tuple(tokens)


def is_bare_identifier(expr: str) -> bool:
    """Whether `expr` is a plain column name."""
    return _BARE_IDENTIFIER_RE.fullmatch(expr) is not None


# --- building -------------------------------------------------------------------


def _description(text: str | None) -> str | None:
    return text if text else None


def _skipped(metric: str, reason: str) -> str:
    return f"semantic views: skipped metric {metric!r}: {reason}."


@dataclass(frozen=True)
class _Key:
    """A join entity of a semantic model, with its column."""

    model: str
    entity: str
    type: EntityType
    column: str


class _Builder:
    def __init__(self, project: SemanticProject) -> None:
        self.warnings: set[str] = set()
        self.models: dict[str, PydanticSemanticModel] = {
            sm.name.lower(): sm for sm in project.semantic_manifest.semantic_models
        }
        root_package = project.dbt_manifest.metadata.project_name
        self.refs = {
            name: render_ref(t.package, t.model_name, t.version, root_package=root_package)
            for name, t in build_ref_map(project).items()
        }
        # model -> join entities, sorted by entity name.
        self.keys: dict[str, list[_Key]] = {
            name: self._keys(sm) for name, sm in self.models.items()
        }
        # entity name -> models where it is a primary or unique key.
        self.parents: dict[str, list[_Key]] = {}
        for keys in self.keys.values():
            for key in keys:
                if key.type in _KEY_TYPES:
                    self.parents.setdefault(key.entity, []).append(key)
        for parents in self.parents.values():
            parents.sort(key=lambda k: k.model)
        metrics = sorted(project.semantic_manifest.metrics, key=lambda m: m.name)
        self.metrics = self._simple_metrics(metrics)
        self.derived = self._derived_metrics(metrics)

    # keys and joins

    def _keys(self, sm: PydanticSemanticModel) -> list[_Key]:
        model = sm.name.lower()
        keys: list[_Key] = []
        for entity in sorted(sm.entities, key=lambda e: e.name):
            if entity.type == EntityType.NATURAL:
                self.warnings.add(
                    f"semantic views: skipped natural entity {entity.name!r} of semantic model "
                    f"{model!r}: natural entities (validity windows) have no semantic view "
                    "equivalent."
                )
                continue
            column = entity.expr or entity.name
            if not is_bare_identifier(column):
                self.warnings.add(
                    f"semantic views: entity {entity.name!r} of semantic model {model!r} is not "
                    f"a plain column ({column!r}), so it is not a key and no join goes through it."
                )
                continue
            keys.append(_Key(model, entity.name, entity.type, column))
        return keys

    def _closure(self, root: str, warnings: set[str]) -> tuple[list[str], list[RelationshipSpec]]:
        # model -> the model it was joined from
        joined_from: dict[str, str | None] = {root: None}
        relationships: list[RelationshipSpec] = []
        frontier = [root]
        for _ in range(MAX_JOIN_HOPS):
            reached: list[str] = []
            for current in frontier:
                for key in self.keys[current]:
                    for parent in self.parents.get(key.entity, []):
                        if parent.model == current:
                            continue
                        if parent.model in joined_from:
                            first = joined_from[parent.model]
                            if first is not None:
                                warnings.add(
                                    f"semantic views: sem__{root} reaches semantic model "
                                    f"{parent.model!r} through {first!r} and {current!r}; kept "
                                    f"the join from {first!r} (a semantic view keeps one join "
                                    "path per table)."
                                )
                            continue
                        joined_from[parent.model] = current
                        reached.append(parent.model)
                        relationships.append(
                            RelationshipSpec(
                                name=f"{current}_to_{parent.model}",
                                child=current,
                                child_column=key.column,
                                parent=parent.model,
                                parent_column=parent.column,
                            )
                        )
            frontier = reached
        return list(joined_from), relationships

    # metrics

    def _simple_metrics(self, metrics: Iterable[PydanticMetric]) -> dict[str, MetricSpec]:
        specs: dict[str, MetricSpec] = {}
        for metric in metrics:
            if metric.type == MetricType.CUMULATIVE:
                self.warnings.add(
                    _skipped(
                        metric.name,
                        "cumulative metrics have no semantic view equivalent "
                        "(an export can materialise one)",
                    )
                )
            elif metric.type == MetricType.CONVERSION:
                self.warnings.add(
                    _skipped(metric.name, "conversion metrics have no semantic view equivalent")
                )
            elif metric.type == MetricType.SIMPLE:
                spec = self._simple_metric(metric)
                if spec is not None:
                    specs[metric.name] = spec
        return specs

    def _simple_metric(self, metric: PydanticMetric) -> MetricSpec | None:
        params = metric.type_params
        agg_params = params.metric_aggregation_params
        if agg_params is None:
            self.warnings.add(_skipped(metric.name, "it has no aggregation parameters"))
            return None
        model = agg_params.semantic_model.lower()
        measure = params.measure
        if metric.filter is not None or (measure is not None and measure.filter is not None):
            self.warnings.add(
                _skipped(metric.name, "metric filters are not supported in semantic views yet")
            )
            return None

        non_additive = None
        nad = agg_params.non_additive_dimension
        if nad is not None:
            if nad.window_groupings:
                self.warnings.add(
                    _skipped(
                        metric.name,
                        "non-additive dimensions with window_groupings have no semantic view "
                        "equivalent",
                    )
                )
                return None
            dimensions = {d.name for d in self.models[model].dimensions}
            if nad.name not in dimensions:
                self.warnings.add(
                    _skipped(
                        metric.name,
                        f"its non-additive dimension {nad.name!r} is not a dimension of "
                        f"semantic model {model!r}",
                    )
                )
                return None
            non_additive = NonAdditiveSpec(
                dimension=nad.name, latest=nad.window_choice == AggregationType.MAX
            )

        percentile = None
        if agg_params.agg == AggregationType.MEDIAN:
            percentile = PercentileSpec(value=0.5, discrete=False, approximate=False)
        elif agg_params.agg == AggregationType.PERCENTILE:
            options = agg_params.agg_params
            if options is None or options.percentile is None:
                self.warnings.add(_skipped(metric.name, "its percentile is not set"))
                return None
            if options.use_discrete_percentile and options.use_approximate_percentile:
                self.warnings.add(
                    _skipped(metric.name, "approximate discrete percentiles are not supported")
                )
                return None
            percentile = PercentileSpec(
                value=options.percentile,
                discrete=options.use_discrete_percentile,
                approximate=options.use_approximate_percentile,
            )

        fills = params.fill_nulls_with is not None or params.join_to_timespine
        if measure is not None:
            fills = fills or measure.fill_nulls_with is not None or measure.join_to_timespine
        if fills:
            self.warnings.add(
                f"semantic views: metric {metric.name!r}: fill_nulls_with and "
                "join_to_timespine have no semantic view equivalent and are ignored."
            )
        return MetricSpec(
            table=model,
            name=metric.name,
            agg=_AGGREGATIONS[agg_params.agg],
            expr=params.expr or metric.name,
            percentile=percentile,
            non_additive=non_additive,
            private=bool(params.is_private),
            comment=_description(metric.description),
        )

    def _derived_metrics(self, metrics: Iterable[PydanticMetric]) -> dict[str, DerivedMetricSpec]:
        by_name = {m.name: m for m in self.metrics.values()}
        specs: dict[str, DerivedMetricSpec] = {}
        kinds = {m.name: m.type for m in metrics}
        for metric in metrics:
            if metric.type not in (MetricType.DERIVED, MetricType.RATIO):
                continue
            params = metric.type_params
            if metric.type == MetricType.RATIO:
                inputs = [i for i in (params.numerator, params.denominator) if i is not None]
            else:
                inputs = list(params.metrics or [])
            reason = self._derived_problem(metric, inputs, by_name, kinds)
            if reason is not None:
                self.warnings.add(_skipped(metric.name, reason))
                continue
            refs = {
                (i.alias or i.name).lower(): MetricRef(by_name[i.name].table, i.name)
                for i in inputs
            }
            if metric.type == MetricType.RATIO:
                numerator, denominator = (refs[(i.alias or i.name).lower()] for i in inputs)
                parts: tuple[str | MetricRef, ...] = (numerator, " / NULLIF(", denominator, ", 0)")
            else:
                parts = _substitute(params.expr or metric.name, refs)
                if not any(isinstance(p, MetricRef) for p in parts):
                    self.warnings.add(
                        _skipped(metric.name, "its expression does not use its inputs")
                    )
                    continue
            specs[metric.name] = DerivedMetricSpec(
                name=metric.name,
                parts=parts,
                private=bool(params.is_private),
                comment=_description(metric.description),
            )
        return specs

    def _derived_problem(
        self,
        metric: PydanticMetric,
        inputs: list[PydanticMetricInput],
        simple: Mapping[str, MetricSpec],
        kinds: Mapping[str, MetricType],
    ) -> str | None:
        kind = "ratio" if metric.type == MetricType.RATIO else "derived"
        if metric.filter is not None:
            return f"{kind} metrics with a filter are not supported in semantic views yet"
        for item in inputs:
            if item.filter is not None:
                return f"its input {item.name!r} has a filter"
            if item.offset_window is not None or item.offset_to_grain is not None:
                return (
                    f"its input {item.name!r} has an offset, which has no semantic view equivalent"
                )
            if item.name not in simple:
                if kinds.get(item.name) == MetricType.SIMPLE:
                    return f"its input {item.name!r} is itself skipped"
                return f"its input {item.name!r} is not a simple metric"
        if len({simple[i.name].table for i in inputs}) > 1:
            return "its inputs come from more than one semantic model"
        return None

    # views

    def view(self, root: str) -> tuple[SemanticViewSpec, set[str]]:
        """
        Describe the semantic view of `root`, with the warnings building it raised.

        The warnings are returned, not added to `self.warnings`, so a view that is
        then skipped reports only why it was skipped.
        """
        warnings: set[str] = set()
        order, relationships = self._closure(root, warnings)
        metrics = sorted(
            (m for m in self.metrics.values() if m.table == root), key=lambda m: m.name
        )
        derived = sorted(
            (d for d in self.derived.values() if _derived_table(d) == root), key=lambda d: d.name
        )
        metric_names = {m.name for m in metrics}
        root_dimensions = {d.name for d in self.models[root].dimensions}
        clashes = sorted(metric_names & root_dimensions)
        if clashes:
            raise SemanticViewError(
                f"metric {clashes[0]!r} has the same name as a dimension of semantic model "
                f"{root!r}; a semantic view cannot hold both. Rename one of them."
            )
        tables: list[TableSpec] = []
        dimensions: list[DimensionSpec] = []
        for alias in order:
            sm = self.models[alias]
            keys = self.keys[alias]
            primary = next((k.column for k in keys if k.type == EntityType.PRIMARY), None)
            unique = sorted({k.column for k in keys if k.type == EntityType.UNIQUE} - {primary})
            tables.append(
                TableSpec(
                    alias=alias,
                    semantic_model=alias,
                    ref=self.refs[alias],
                    primary_key=primary,
                    unique_keys=tuple(unique),
                    comment=_description(sm.description),
                )
            )
            taken = {d.name for d in sm.dimensions}
            if alias == root:
                taken |= metric_names
            table_dims = [
                DimensionSpec(alias, d.name, d.expr or d.name, _description(d.description))
                for d in sm.dimensions
            ]
            table_dims += [
                DimensionSpec(alias, e.name, e.expr or e.name, _description(e.description))
                for e in sm.entities
                if e.type in _JOIN_TYPES and e.name not in taken
            ]
            dimensions += sorted(table_dims, key=lambda d: d.name)
        spec = SemanticViewSpec(
            name=root,
            tables=tuple(tables),
            relationships=tuple(relationships),
            dimensions=tuple(dimensions),
            metrics=tuple(metrics),
            derived_metrics=tuple(derived),
            comment=_description(self.models[root].description),
        )
        return spec, warnings


def _derived_table(metric: DerivedMetricSpec) -> str:
    return next(p.table for p in metric.parts if isinstance(p, MetricRef))


def _substitute(expr: str, refs: Mapping[str, MetricRef]) -> tuple[str | MetricRef, ...]:
    """Replace each bare reference to an input metric in `expr` with a `MetricRef`."""
    parts: list[str | MetricRef] = []
    for token in scan_sql(expr):
        ref = None
        if token.identifier and not token.qualified and not token.call:
            ref = refs.get(token.text.lower())
        if ref is not None:
            parts.append(ref)
        elif parts and isinstance(parts[-1], str):
            parts[-1] += token.text
        else:
            parts.append(token.text)
    return tuple(parts)


def build_semantic_views(project: SemanticProject) -> SemanticViewBuild:
    """Describe one semantic view per semantic model, sorted by name."""
    builder = _Builder(project)
    views: list[SemanticViewSpec] = []
    warnings = set(builder.warnings)
    for name in sorted(builder.models):
        view, view_warnings = builder.view(name)
        views.append(view)
        warnings |= view_warnings
    return SemanticViewBuild(views=tuple(views), warnings=tuple(sorted(warnings)))


# --- models for the output directory ------------------------------------------------


@dataclass(frozen=True)
class SemanticViewModel:
    """One generated semantic view model: where it goes and what it says."""

    semantic_model: str
    # sem__<semantic_model>.sql
    file_name: str
    # The model name, so the relation is never the semantic model's own table.
    alias: str
    # Generated models set no schema: the output folder's `+schema` applies.
    schema: str | None
    description: str | None
    # `{{ config(...) }}`, a blank line, the clauses, and a final newline.
    body: str


@dataclass(frozen=True)
class SemanticViewResult:
    """The semantic view models of a project, sorted by file name, and its warnings."""

    models: tuple[SemanticViewModel, ...]
    warnings: tuple[str, ...]


def render_semantic_views(project: SemanticProject, *, adapter: str) -> SemanticViewResult:
    """
    Render a semantic view model per semantic model for `adapter`, or warn it has none yet.

    A semantic model whose view cannot be built (`SemanticViewError`) is left out
    with a warning naming the view and the reason; the other views still render.
    """
    from dbt_s_layer import snowflake

    if not project.semantic_manifest.semantic_models:
        return SemanticViewResult(models=(), warnings=())
    renderers = {"snowflake": snowflake.render_snowflake}
    render = renderers.get(adapter)
    if render is None:
        return SemanticViewResult(
            models=(),
            warnings=(
                f"semantic views: not generated for adapter {adapter!r} yet; only exports "
                "were generated.",
            ),
        )
    builder = _Builder(project)
    models: list[SemanticViewModel] = []
    warnings = set(builder.warnings)
    for name in sorted(builder.models):
        # One semantic model Snowflake cannot hold must not stop the others, or the exports.
        try:
            view, view_warnings = builder.view(name)
            body = render(view)
        except SemanticViewError as exc:
            # Only the skip is reported: the view's own warnings describe a view not written.
            reason = str(exc).removeprefix(f"sem__{name}: ")
            warnings.add(f"semantic views: skipped sem__{name}: {reason}")
            continue
        warnings |= view_warnings
        models.append(
            SemanticViewModel(
                semantic_model=view.name,
                file_name=f"sem__{view.name}.sql",
                alias=f"sem__{view.name}",
                schema=None,
                description=view.comment,
                body=body,
            )
        )
    if (
        adapter == "snowflake"
        and snowflake.MATERIALIZATION_MACRO not in project.dbt_manifest.macros
    ):
        warnings.add(
            f"semantic views: the {snowflake.PACKAGE} package is not installed (manifest.json "
            f"has no {snowflake.MATERIALIZATION_MACRO}). Add Snowflake-Labs/{snowflake.PACKAGE} "
            "to packages.yml and run `dbt deps`; until then `dbt run` fails on the sem__ models."
        )
    return SemanticViewResult(models=tuple(models), warnings=tuple(sorted(warnings)))
