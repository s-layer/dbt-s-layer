"""
Read and validate the `config.meta.s_layer` blocks of a project (the S-LAYER vocabulary).

A block says what a metric or dimension means: a currency in USD, a fraction shown
as a percentage, a monthly date. The vocabulary, its validation and its normal form
live in `s_layer_semantic_formats`; this module finds the blocks in the semantic
manifest, checks each against its member and against what dbt already declares,
and refuses the project if any is wrong, so a bad block never reaches a warehouse.

Blocks are read from metrics, dimensions and entities only. Measures are never
read: Fusion copies a metric's meta onto the measure it generates for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from metricflow_semantic_interfaces.type_enums.dimension_type import DimensionType
from s_layer_semantic_formats import (
    TEMPORAL_KINDS,
    MemberType,
    SLayerBlock,
    SLayerValidationError,
    validate_block,
)

from dbt_s_layer.errors import SLayerBlockError

if TYPE_CHECKING:
    from metricflow_semantic_interfaces.implementations.element_config import (
        PydanticSemanticLayerElementConfig,
    )
    from metricflow_semantic_interfaces.implementations.metric import PydanticMetric

    from dbt_s_layer.project import SemanticProject

META_KEY = "s_layer"


@dataclass(frozen=True)
class SLayerBlocks:
    """The valid blocks of a project, by member."""

    # metric name -> block
    metrics: Mapping[str, SLayerBlock] = field(default_factory=dict[str, SLayerBlock])
    # (semantic model, dimension name) -> block
    dimensions: Mapping[tuple[str, str], SLayerBlock] = field(
        default_factory=dict[tuple[str, str], SLayerBlock]
    )
    # (semantic model, entity name) -> block
    entities: Mapping[tuple[str, str], SLayerBlock] = field(
        default_factory=dict[tuple[str, str], SLayerBlock]
    )


def _raw_block(config: PydanticSemanticLayerElementConfig | None) -> object:
    return None if config is None else config.meta.get(META_KEY)


def _mapping(raw: object) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"must be a mapping, not {type(raw).__name__}")
    return cast("Mapping[str, Any]", raw)


def _validate(raw: Mapping[str, Any], member_type: MemberType) -> SLayerBlock:
    """Validate `raw`, raising `SLayerValidationError` with one line per problem."""
    block = validate_block(raw, member_type)
    if block.label is not None:
        raise ValueError("label: set the member's dbt `label:` instead")
    return block


def _with_grain(block: SLayerBlock, raw: Mapping[str, Any], grain: str) -> SLayerBlock:
    """Check a time dimension's block against its dbt granularity, filling the grain."""
    if block.kind not in TEMPORAL_KINDS:
        raise ValueError(
            f"value.kind: a time dimension needs kind date or datetime, not {block.kind!r}"
        )
    stated = None if block.time is None else block.time.grain
    if stated is None:
        time = {**dict(raw.get("time") or {}), "grain": grain}
        return validate_block({**raw, "time": time}, "dimension")
    if stated != grain:
        raise ValueError(
            f"time.grain: {stated!r} contradicts the dimension's time_granularity {grain!r}; "
            "leave it out to use dbt's"
        )
    return block


def _check_metric(block: SLayerBlock, metric: PydanticMetric) -> None:
    params = metric.type_params.metric_aggregation_params
    nad = None if params is None else params.non_additive_dimension
    if block.aggregation is not None and nad is not None:
        raise ValueError(
            "aggregation: the metric's non_additive_dimension already says how it adds "
            "up; leave aggregation out"
        )


def _issues(error: Exception) -> list[str]:
    if isinstance(error, SLayerValidationError):
        return [str(issue) for issue in error.issues]
    return [str(error)]


def read_s_layer_blocks(project: SemanticProject) -> SLayerBlocks:
    """
    Read every `s_layer` block of the project's metrics, dimensions and entities.

    Raises `SLayerBlockError` naming every invalid block and why, so one run
    reports all of them.
    """
    manifest = project.semantic_manifest
    problems: list[str] = []
    metrics: dict[str, SLayerBlock] = {}
    dimensions: dict[tuple[str, str], SLayerBlock] = {}
    entities: dict[tuple[str, str], SLayerBlock] = {}

    for metric in sorted(manifest.metrics, key=lambda m: m.name):
        raw = _raw_block(metric.config)
        if raw is None:
            continue
        try:
            raw = _mapping(raw)
            block = _validate(raw, "metric")
            _check_metric(block, metric)
        except (SLayerValidationError, ValueError) as exc:
            problems += [f"metric {metric.name!r}: {issue}" for issue in _issues(exc)]
            continue
        metrics[metric.name] = block

    for semantic_model in sorted(manifest.semantic_models, key=lambda sm: sm.name):
        model = semantic_model.name.lower()
        for dimension in sorted(semantic_model.dimensions, key=lambda d: d.name):
            raw = _raw_block(dimension.config)
            if raw is None:
                continue
            where = f"dimension {dimension.name!r} of semantic model {model!r}"
            try:
                raw = _mapping(raw)
                block = _validate(raw, "dimension")
                params = dimension.type_params
                if dimension.type == DimensionType.TIME and params is not None:
                    grain = params.time_granularity.value
                    block = _with_grain(block, raw, grain)
            except (SLayerValidationError, ValueError) as exc:
                problems += [f"{where}: {issue}" for issue in _issues(exc)]
                continue
            dimensions[(model, dimension.name)] = block
        for entity in sorted(semantic_model.entities, key=lambda e: e.name):
            raw = _raw_block(entity.config)
            if raw is None:
                continue
            try:
                block = _validate(_mapping(raw), "dimension")
            except (SLayerValidationError, ValueError) as exc:
                where = f"entity {entity.name!r} of semantic model {model!r}"
                problems += [f"{where}: {issue}" for issue in _issues(exc)]
                continue
            entities[(model, entity.name)] = block

    if problems:
        count = len(problems)
        raise SLayerBlockError(
            f"invalid s_layer block{'' if count == 1 else 's'} ({count} "
            f"problem{'' if count == 1 else 's'}): " + "; ".join(problems)
        )
    return SLayerBlocks(metrics=metrics, dimensions=dimensions, entities=entities)
