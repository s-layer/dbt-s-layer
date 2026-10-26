"""Tests for the S-LAYER tags on DuckDB semantic view members (extension 0.17+)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import jinja2
import pytest
from s_layer_semantic_formats import validate_block

from dbt_s_layer.duckdb_views import CONFIG, TAGS_SWITCH, render_duckdb
from dbt_s_layer.errors import SLayerBlockError
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import (
    DerivedMetricSpec,
    DimensionSpec,
    MetricRef,
    MetricSpec,
    SemanticViewSpec,
    TableSpec,
    render_semantic_views,
)
from tests.conftest import DUCKDB_SETS, MakeProject

ORDERS = TableSpec(
    alias="orders", semantic_model="orders", ref="{{ ref('orders') }}", primary_key="order_id"
)
REVENUE = MetricSpec(table="orders", name="revenue", agg="sum", expr="amount")
REGION = DimensionSpec(table="orders", name="region", expr="region")


def _block(raw: dict[str, Any], member: str = "metric") -> Any:
    return validate_block(raw, "metric" if member == "metric" else "dimension")


def _view(
    *,
    dimensions: tuple[DimensionSpec, ...] = (REGION,),
    metrics: tuple[MetricSpec, ...] = (REVENUE,),
    derived_metrics: tuple[DerivedMetricSpec, ...] = (),
) -> SemanticViewSpec:
    return SemanticViewSpec(
        name="orders",
        tables=(ORDERS,),
        dimensions=dimensions,
        metrics=metrics,
        derived_metrics=derived_metrics,
    )


def _sql(body: str, *, tags: bool = True) -> str:
    """The body as dbt compiles it, with `s_layer_duckdb_tags` set to `tags`."""

    def var(name: str, default: object = None) -> object:
        assert name == "s_layer_duckdb_tags"
        return tags

    def ref(name: str) -> str:
        return name

    def config(**_: object) -> str:
        return ""

    env = jinja2.Environment(autoescape=False)
    return env.from_string(body).render(ref=ref, config=config, var=var)


def _line(sql: str, clause: str) -> str:
    return sql.split(f"{clause} (\n", 1)[1].split("\n", 1)[0].strip()


def test_untagged_view_is_unchanged() -> None:
    body = render_duckdb(_view()).body
    assert "S_LAYER" not in body
    assert "s_layer_tags" not in body
    assert body.startswith(CONFIG + "\n\nTABLES")


def test_tagged_view_reads_the_switch_once() -> None:
    region = replace(REGION, s_layer=_block({"value": {"kind": "category"}}, "dimension"))
    body = render_duckdb(_view(dimensions=(region,))).body
    assert body.startswith(CONFIG + "\n{%- set s_layer_tags = " + TAGS_SWITCH + " %}\n\nTABLES")
    assert TAGS_SWITCH == "var('s_layer_duckdb_tags', true)"


def test_kind_only_dimension() -> None:
    region = replace(REGION, s_layer=_block({"value": {"kind": "category"}}, "dimension"))
    sql = _sql(render_duckdb(_view(dimensions=(region,))).body)
    assert _line(sql, "DIMENSIONS") == (
        "orders.region AS orders.region WITH TAG (S_LAYER_KIND = 'category')"
    )


def test_every_slot() -> None:
    block = _block(
        {
            "value": {"kind": "currency", "currency": "USD"},
            "display": {"decimals": {"max": 0}},
            "synonyms": ["sales", "turnover"],
        }
    )
    revenue = replace(REVENUE, label="Revenue", comment="Gross revenue.", s_layer=block)
    sql = _sql(render_duckdb(_view(metrics=(revenue,))).body)
    spec = (
        '{"display":{"decimals":[0,0],"grouping":true,"negative":"minus"},'
        '"value":{"currency":"USD"}}'
    )
    assert _line(sql, "METRICS") == (
        "orders.revenue AS SUM(orders.amount) COMMENT = 'Gross revenue.' "
        "WITH SYNONYMS = ('sales', 'turnover') "
        f"WITH TAG (S_LAYER_KIND = 'currency', S_LAYER_LABEL = 'Revenue', S_LAYER_SPEC = '{spec}')"
    )


def test_the_switch_turns_tags_off() -> None:
    """Extensions before 0.17 have no TAG clause; synonyms and comments stay."""
    block = _block({"value": {"kind": "currency", "currency": "USD"}, "synonyms": ["sales"]})
    revenue = replace(REVENUE, label="Revenue", comment="Gross revenue.", s_layer=block)
    sql = _sql(render_duckdb(_view(metrics=(revenue,))).body, tags=False)
    assert _line(sql, "METRICS") == (
        "orders.revenue AS SUM(orders.amount) COMMENT = 'Gross revenue.' WITH SYNONYMS = ('sales')"
    )
    assert "TAG" not in sql


def test_label_without_a_block_is_not_tagged() -> None:
    body = render_duckdb(_view(metrics=(replace(REVENUE, label="Revenue"),))).body
    assert "S_LAYER" not in body


def test_derived_metric_is_tagged() -> None:
    share = DerivedMetricSpec(
        name="share",
        parts=(MetricRef("orders", "revenue"), " / 100"),
        s_layer=_block({"value": {"kind": "percentage"}}),
    )
    sql = _sql(render_duckdb(_view(derived_metrics=(share,))).body)
    assert "share AS revenue / 100 WITH TAG (S_LAYER_KIND = 'percentage', " in sql


def test_tag_values_are_escaped() -> None:
    block = _block(
        {"value": {"kind": "category"}, "x-acme": {"note": "it's {{ not jinja }}"}},
        "dimension",
    )
    region = replace(REGION, label="Région's", s_layer=block)
    body = render_duckdb(_view(dimensions=(region,))).body
    line = _line(_sql(body), "DIMENSIONS")
    assert "S_LAYER_LABEL = 'Région''s'" in line
    assert "it''s {{ not jinja }}" in line


def test_spec_over_the_tag_limit_is_an_error() -> None:
    block = _block({"value": {"kind": "category"}, "x-acme": {"note": "x" * 300}}, "dimension")
    region = replace(REGION, s_layer=block)
    with pytest.raises(SLayerBlockError, match="dimension 'region'.*256"):
        render_duckdb(_view(dimensions=(region,)))


def test_label_over_the_tag_limit_is_an_error() -> None:
    region = replace(
        REGION, label="x" * 257, s_layer=_block({"value": {"kind": "category"}}, "dimension")
    )
    with pytest.raises(SLayerBlockError, match="dimension 'region'.*S_LAYER_LABEL.*256"):
        render_duckdb(_view(dimensions=(region,)))


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_fixture_views_carry_tags_and_need_no_hook(
    set_name: str, make_project: MakeProject
) -> None:
    result = render_semantic_views(load_project(make_project(set_name)), adapter="duckdb")
    assert any("S_LAYER_KIND = " in m.body for m in result.models)
    assert not [w for w in result.warnings if "on-run-start" in w]
