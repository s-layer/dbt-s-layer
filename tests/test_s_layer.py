"""Tests for reading and validating `config.meta.s_layer` blocks (the S-LAYER vocabulary)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from s_layer_semantic_formats import validate_block

from dbt_s_layer.errors import SLayerBlockError
from dbt_s_layer.output import render_output
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import SemanticViewSpec, build_semantic_views
from tests.conftest import ARTIFACT_SETS
from tests.test_semantic_views import _simple

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import MakeProject

Raw = dict[str, Any]

USD = {"value": {"kind": "currency", "currency": "USD"}}
CATEGORY = {"value": {"kind": "category"}}


def _semantic(project_dir: Path, mutate: Callable[[Raw], None]) -> None:
    path = project_dir / "target" / "semantic_manifest.json"
    raw: Raw = json.loads(path.read_text())
    mutate(raw)
    path.write_text(json.dumps(raw))


def _named(items: list[Raw], name: str) -> Raw:
    return next(item for item in items if item["name"] == name)


def _set_meta(item: Raw, block: object) -> None:
    item["config"] = {"meta": {"s_layer": block}}


def _on_metric(name: str, block: object) -> Callable[[Raw], None]:
    return lambda raw: _set_meta(_named(raw["metrics"], name), block)


def _on_dimension(model: str, name: str, block: object) -> Callable[[Raw], None]:
    return lambda raw: _set_meta(
        _named(_named(raw["semantic_models"], model)["dimensions"], name), block
    )


def _on_entity(model: str, name: str, block: object) -> Callable[[Raw], None]:
    return lambda raw: _set_meta(
        _named(_named(raw["semantic_models"], model)["entities"], name), block
    )


def _view(project_dir: Path, name: str) -> SemanticViewSpec:
    build = build_semantic_views(load_project(project_dir))
    return next(v for v in build.views if v.name == name)


def _error(project_dir: Path) -> str:
    with pytest.raises(SLayerBlockError) as caught:
        build_semantic_views(load_project(project_dir))
    return str(caught.value)


# --- reading ------------------------------------------------------------------------


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_no_blocks_by_default(set_name: str, make_project: MakeProject) -> None:
    view = _view(make_project(set_name), "orders")
    assert all(d.s_layer is None for d in view.dimensions)
    assert all(m.s_layer is None for m in view.metrics)


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_metric_block(set_name: str, make_project: MakeProject) -> None:
    project_dir = make_project(set_name)
    _semantic(project_dir, _on_metric("revenue", USD))
    view = _view(project_dir, "orders")
    revenue = _named_spec(view.metrics, "revenue")
    assert revenue.s_layer == validate_block(USD, "metric")


def test_dimension_block(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_dimension("orders", "ordered_at", {"value": {"kind": "date"}}))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer is not None
    assert ordered_at.s_layer.kind == "date"


def test_entity_block(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_entity("orders", "order", {"value": {"kind": "identifier"}}))
    order = _named_spec(_view(project_dir, "orders").dimensions, "order")
    assert order.s_layer == validate_block({"value": {"kind": "identifier"}}, "dimension")


def test_fusion_column_meta_block(make_project: MakeProject) -> None:
    """A block in a column's `config.meta` reaches the dimension on Fusion too."""
    project_dir = make_project("fusion_new")
    path = project_dir / "target" / "manifest.json"
    raw: Raw = json.loads(path.read_text())
    for node in raw["nodes"].values():
        column = node.get("columns", {}).get("ordered_at")
        if column is not None and column.get("dimension") is not None:
            column.setdefault("config", {})["meta"] = {"s_layer": {"value": {"kind": "date"}}}
    path.write_text(json.dumps(raw))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer is not None
    assert ordered_at.s_layer.kind == "date"


def test_time_dimension_grain_comes_from_dbt(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_dimension("orders", "ordered_at", {"value": {"kind": "date"}}))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer is not None
    assert ordered_at.s_layer.time is not None
    assert ordered_at.s_layer.time.grain == "day"


def test_time_dimension_with_the_same_grain_is_accepted(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    block = {"value": {"kind": "date"}, "time": {"grain": "day", "fiscal_year_start": 4}}
    _semantic(project_dir, _on_dimension("orders", "ordered_at", block))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer == validate_block(block, "dimension")


def test_measure_meta_is_ignored(make_project: MakeProject) -> None:
    """Fusion copies a metric's meta onto its generated measure; measures are never read."""
    project_dir = make_project("core_legacy")

    def on_measures(raw: Raw) -> None:
        for semantic_model in raw["semantic_models"]:
            for measure in semantic_model["measures"]:
                _set_meta(measure, {"value": {"kind": "not-a-kind"}})

    _semantic(project_dir, on_measures)
    view = _view(project_dir, "orders")
    assert all(m.s_layer is None for m in view.metrics)


def test_other_meta_keys_are_ignored(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(
        project_dir,
        lambda raw: _named(raw["metrics"], "revenue").update(config={"meta": {"owner": "finance"}}),
    )
    assert _named_spec(_view(project_dir, "orders").metrics, "revenue").s_layer is None


# --- rejecting ----------------------------------------------------------------------


def test_invalid_block_is_an_error(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", {"value": {"kind": "currency"}}))
    message = _error(project_dir)
    assert "metric 'revenue'" in message
    assert "value.currency" in message


def test_every_invalid_block_is_reported(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", {"value": {"kind": "currency"}}))
    _semantic(project_dir, _on_dimension("orders", "ordered_at", {"value": {}}))
    message = _error(project_dir)
    assert "metric 'revenue'" in message
    assert "dimension 'ordered_at' of semantic model 'orders'" in message


def test_block_must_be_a_mapping(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", "currency"))
    assert "metric 'revenue'" in _error(project_dir)


def test_kind_must_fit_the_member_type(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", CATEGORY))
    message = _error(project_dir)
    assert "metric 'revenue'" in message
    assert "category" in message


def test_label_belongs_in_dbt(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", {**USD, "label": "Sales"}))
    message = _error(project_dir)
    assert "metric 'revenue'" in message
    assert "label" in message


def test_time_dimension_needs_a_temporal_kind(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_dimension("orders", "ordered_at", CATEGORY))
    message = _error(project_dir)
    assert "dimension 'ordered_at' of semantic model 'orders'" in message
    assert "date or datetime" in message


def test_time_dimension_grain_must_match_dbt(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    block = {"value": {"kind": "date"}, "time": {"grain": "month"}}
    _semantic(project_dir, _on_dimension("orders", "ordered_at", block))
    message = _error(project_dir)
    assert "time.grain" in message
    assert "'day'" in message


def test_non_additive_metric_states_no_aggregation(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")

    def mutate(raw: Raw) -> None:
        revenue = _named(raw["metrics"], "revenue")
        revenue["type_params"]["metric_aggregation_params"]["non_additive_dimension"] = {
            "name": "ordered_at",
            "window_choice": "max",
            "window_groupings": [],
        }
        _set_meta(revenue, {**USD, "aggregation": {"additivity": "additive"}})

    _semantic(project_dir, mutate)
    message = _error(project_dir)
    assert "aggregation" in message
    assert "non_additive_dimension" in message


def test_invalid_block_stops_render_for_any_adapter(make_project: MakeProject) -> None:
    """Exports-only adapters still refuse a project with a bad block."""
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_metric("revenue", {"value": {"kind": "currency"}}))
    with pytest.raises(SLayerBlockError):
        render_output(load_project(project_dir), adapter="duckdb")


def _named_spec(items: Any, name: str) -> Any:
    return next(item for item in items if item.name == name)


def test_derived_metric_block(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")

    def mutate(raw: Raw) -> None:
        share = {"value": {"kind": "percentage"}}
        ratio: Raw = {
            "name": "revenue_share",
            "label": "revenue_share",
            "description": None,
            "filter": None,
            "metadata": None,
            "config": {"meta": {"s_layer": share}},
            "time_granularity": None,
            "type": "ratio",
            "type_params": {
                "numerator": _metric_input("revenue"),
                "denominator": _metric_input("revenue"),
                "input_measures": [],
            },
        }
        raw["metrics"].append(ratio)

    _semantic(project_dir, mutate)
    view = _view(project_dir, "orders")
    share = _named_spec(view.derived_metrics, "revenue_share")
    assert share.s_layer == validate_block({"value": {"kind": "percentage"}}, "metric")


def _metric_input(name: str) -> Raw:
    return {
        "name": name,
        "filter": None,
        "alias": None,
        "offset_window": None,
        "offset_to_grain": None,
    }


def _with_granularity(grain: str, block: object) -> Callable[[Raw], None]:
    def mutate(raw: Raw) -> None:
        orders = _named(raw["semantic_models"], "orders")
        ordered_at = _named(orders["dimensions"], "ordered_at")
        ordered_at["type_params"]["time_granularity"] = grain
        _set_meta(ordered_at, block)

    return mutate


def test_sub_day_grain_is_filled(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _with_granularity("hour", {"value": {"kind": "datetime"}}))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer is not None
    assert ordered_at.s_layer.time is not None
    assert ordered_at.s_layer.time.grain == "hour"


def test_sub_second_grain_is_left_unstated(make_project: MakeProject) -> None:
    """The vocabulary stops at seconds, so a finer dbt grain is not copied into the block."""
    project_dir = make_project("core_new")
    _semantic(project_dir, _with_granularity("millisecond", {"value": {"kind": "datetime"}}))
    ordered_at = _named_spec(_view(project_dir, "orders").dimensions, "ordered_at")
    assert ordered_at.s_layer == validate_block({"value": {"kind": "datetime"}}, "dimension")


def test_date_kind_on_a_sub_day_dimension(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _with_granularity("hour", {"value": {"kind": "date"}}))
    message = _error(project_dir)
    assert "time_granularity 'hour'" in message
    assert "use kind datetime" in message


def test_invalid_entity_block(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    _semantic(project_dir, _on_entity("orders", "order", USD))
    message = _error(project_dir)
    assert "entity 'order' of semantic model 'orders'" in message


def test_legacy_non_additive_measure_states_no_aggregation(make_project: MakeProject) -> None:
    """On the legacy spec the measure's non_additive_dimension reaches the metric too."""
    project_dir = make_project("core_legacy")

    def mutate(raw: Raw) -> None:
        orders = _named(raw["semantic_models"], "orders")
        _named(orders["measures"], "order_total")["non_additive_dimension"] = {
            "name": "ordered_at",
            "window_choice": "max",
            "window_groupings": [],
        }
        _set_meta(
            _named(raw["metrics"], "revenue"), {**USD, "aggregation": {"additivity": "additive"}}
        )

    _semantic(project_dir, mutate)
    assert "non_additive_dimension" in _error(project_dir)


def test_entity_block_left_out_of_the_view_warns(make_project: MakeProject) -> None:
    """An entity that gives way to a metric is not a member, so its block would vanish."""
    project_dir = make_project("fusion_new")

    def mutate(raw: Raw) -> None:
        raw["metrics"].append(_simple(raw, "customer", "orders", "count_distinct", "x"))
        _on_entity("orders", "customer", {"value": {"kind": "identifier"}})(raw)

    _semantic(project_dir, mutate)
    build = build_semantic_views(load_project(project_dir))
    assert any("entity 'customer'" in w and "s_layer block" in w for w in build.warnings), (
        build.warnings
    )
