"""Tests for the S-LAYER synonyms and Unity Catalog column tags on Databricks metric views."""

from __future__ import annotations

import json
from dataclasses import replace
from io import StringIO
from typing import TYPE_CHECKING, Any

import jinja2
import pytest
from ruamel.yaml import YAML
from s_layer_semantic_formats import to_compact_json, validate_block

from dbt_s_layer.databricks import CONFIG, render_databricks
from dbt_s_layer.errors import SLayerBlockError
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import (
    DerivedMetricSpec,
    DimensionSpec,
    MetricRef,
    MetricSpec,
    RelationshipSpec,
    SemanticViewSpec,
    TableSpec,
    render_semantic_views,
)
from tests.conftest import DATABRICKS_SETS

if TYPE_CHECKING:
    from tests.conftest import MakeProject

ORDERS = TableSpec(alias="orders", semantic_model="orders", ref="{{ ref('orders') }}")
CUSTOMERS = TableSpec(alias="customers", semantic_model="customers", ref="{{ ref('customers') }}")
TO_CUSTOMERS = RelationshipSpec(
    name="orders_to_customers",
    child="orders",
    child_column="customer_id",
    parent="customers",
    parent_column="id",
    entity="customer",
)
REVENUE = MetricSpec(table="orders", name="revenue", agg="sum", expr="amount")
STATUS = DimensionSpec(table="orders", name="status", expr="status")
CATEGORY = {"value": {"kind": "category"}}
THIS = "`main`.`marts`.`sem__orders`"


def _block(raw: dict[str, Any], member: str = "metric") -> Any:
    return validate_block(raw, "metric" if member == "metric" else "dimension")


def _view(
    *,
    tables: tuple[TableSpec, ...] = (ORDERS,),
    relationships: tuple[RelationshipSpec, ...] = (),
    dimensions: tuple[DimensionSpec, ...] = (STATUS,),
    metrics: tuple[MetricSpec, ...] = (REVENUE,),
    derived_metrics: tuple[DerivedMetricSpec, ...] = (),
) -> SemanticViewSpec:
    return SemanticViewSpec(
        name="orders",
        tables=tables,
        relationships=relationships,
        dimensions=dimensions,
        metrics=metrics,
        derived_metrics=derived_metrics,
    )


def _render(body: str) -> tuple[dict[str, Any], Any]:
    """Render as dbt would: the config() call's arguments, and the definition as YAML."""
    captured: dict[str, Any] = {}

    def config(**kwargs: Any) -> str:
        captured.update(kwargs)
        return ""

    def ref(*args: str, **_: object) -> str:
        return f"`main`.`marts`.`{args[-1]}`"

    text = jinja2.Environment(autoescape=False).from_string(body).render(config=config, ref=ref)
    return captured, YAML(typ="safe", pure=True).load(StringIO(text))  # pyright: ignore[reportUnknownMemberType]


def _hooks(body: str) -> list[str]:
    """The post-hooks as dbt runs them: each rendered again, with `this` the metric view."""
    config, _ = _render(body)
    environment = jinja2.Environment(autoescape=False)
    return [environment.from_string(hook).render(this=THIS) for hook in config["post_hook"]]


def _field(definition: Any, section: str, name: str) -> dict[str, Any]:
    return next(item for item in definition[section] if item["name"] == name)


def test_untagged_view_is_unchanged() -> None:
    body = render_databricks(_view()).body
    assert body.startswith(CONFIG + "\n\n")
    assert "S_LAYER" not in body
    assert "synonyms" not in body


def test_tagged_view_rebuilds_and_tags_after_every_run() -> None:
    status = replace(STATUS, s_layer=_block(CATEGORY, "dimension"))
    body = render_databricks(_view(dimensions=(status,))).body
    config, _ = _render(body)
    assert config["materialized"] == "metric_view"
    assert config["static_analysis"] == "off"
    # CREATE OR REPLACE wipes column tags; ALTER VIEW could leave stale ones behind.
    assert config["view_update_via_alter"] is False
    assert _hooks(body) == [
        f"alter table {THIS} alter column `status` set tags ('S_LAYER_KIND' = 'category')"
    ]


def test_spec_and_synonyms() -> None:
    block = _block(
        {
            "value": {"kind": "currency", "currency": "USD"},
            "display": {"decimals": {"max": 0}},
            "synonyms": ["sales", "turnover"],
        }
    )
    revenue = replace(REVENUE, label="Revenue", s_layer=block)
    body = render_databricks(_view(metrics=(revenue,))).body
    spec = (
        '{"display":{"decimals":[0,0],"grouping":true,"negative":"minus"},'
        '"value":{"currency":"USD"}}'
    )
    assert _hooks(body) == [
        f"alter table {THIS} alter column `revenue` set tags "
        f"('S_LAYER_KIND' = 'currency', 'S_LAYER_SPEC' = '{spec}')"
    ]
    _, definition = _render(body)
    measure = _field(definition, "measures", "revenue")
    assert measure["display_name"] == "Revenue"
    assert measure["synonyms"] == ["sales", "turnover"]


def test_label_is_the_display_name_not_a_tag() -> None:
    revenue = replace(REVENUE, label="Revenue", s_layer=_block({"value": {"kind": "count"}}))
    body = render_databricks(_view(metrics=(revenue,))).body
    assert "S_LAYER_LABEL" not in body


def test_joined_dimension_is_tagged_under_its_view_name() -> None:
    country = DimensionSpec(
        table="customers", name="country", expr="country", s_layer=_block(CATEGORY, "dimension")
    )
    view = _view(
        tables=(ORDERS, CUSTOMERS), relationships=(TO_CUSTOMERS,), dimensions=(STATUS, country)
    )
    body = render_databricks(view).body
    assert _hooks(body) == [
        f"alter table {THIS} alter column `customer__country` "
        "set tags ('S_LAYER_KIND' = 'category')"
    ]


def test_derived_metric_is_tagged() -> None:
    block = _block({"value": {"kind": "percentage"}})
    share = DerivedMetricSpec(
        name="share", parts=(MetricRef("orders", "revenue"), " / 100"), s_layer=block
    )
    body = render_databricks(_view(derived_metrics=(share,))).body
    spec = to_compact_json(block)
    assert _hooks(body) == [
        f"alter table {THIS} alter column `share` set tags "
        f"('S_LAYER_KIND' = 'percentage', 'S_LAYER_SPEC' = '{spec}')"
    ]


def test_values_survive_sql_jinja_and_yaml() -> None:
    note = "it's a \\ {{ not jinja }} {% nor this %} $$"
    block = _block(
        {"value": {"kind": "category"}, "synonyms": ["l'état {{ x }}"], "x-acme": {"note": note}},
        "dimension",
    )
    status = replace(STATUS, name="sta`tus", expr="status", s_layer=block)
    body = render_databricks(_view(dimensions=(status,))).body
    [hook] = _hooks(body)
    spec = json.dumps({"x-acme": {"note": note}}, separators=(",", ":"), ensure_ascii=False)
    sql_spec = spec.replace("\\", "\\\\").replace("'", "\\'")
    assert hook == (
        f"alter table {THIS} alter column `sta``tus` "
        f"set tags ('S_LAYER_KIND' = 'category', 'S_LAYER_SPEC' = '{sql_spec}')"
    )
    _, definition = _render(body)
    assert _field(definition, "dimensions", "sta`tus")["synonyms"] == ["l'état {{ x }}"]


def test_every_limit_is_checked_and_reported_together() -> None:
    long_spec = {"value": {"kind": "category"}, "x-acme": {"note": "x" * 300}}
    status = replace(STATUS, s_layer=_block(long_spec, "dimension"))
    many = {"value": {"kind": "count"}, "synonyms": [f"s{i}" for i in range(11)]}
    revenue = replace(REVENUE, s_layer=_block(many))
    entity = DimensionSpec(
        table="orders",
        name="order",
        expr="order_id",
        entity=True,
        s_layer=_block({"value": {"kind": "identifier"}, "synonyms": ["x" * 256]}, "dimension"),
    )
    with pytest.raises(SLayerBlockError) as caught:
        render_databricks(_view(dimensions=(status, entity), metrics=(revenue,)))
    problems = caught.value.problems
    assert len(problems) == 3
    assert any(p.startswith("dimension 'status' of semantic model 'orders'") for p in problems)
    assert any(p.startswith("metric 'revenue'") and "11 synonyms" in p for p in problems)
    assert any(p.startswith("entity 'order' of semantic model 'orders'") for p in problems)


@pytest.mark.parametrize("set_name", DATABRICKS_SETS)
def test_long_values_stop_generate_once_each(set_name: str, make_project: MakeProject) -> None:
    project_dir = make_project(set_name)
    path = project_dir / "target" / "semantic_manifest.json"
    raw = json.loads(path.read_text())
    revenue = next(m for m in raw["metrics"] if m["name"] == "revenue")
    long_spec = {"value": {"kind": "count"}, "x-acme": {"note": "x" * 300}}
    revenue["config"] = {"meta": {"s_layer": long_spec}}
    path.write_text(json.dumps(raw))
    with pytest.raises(SLayerBlockError) as caught:
        render_semantic_views(load_project(project_dir), adapter="databricks")
    assert str(caught.value).startswith("tag values too long for Databricks (1 problem): ")
    assert caught.value.problems == (caught.value.problems[0],)
    assert caught.value.problems[0].startswith("metric 'revenue': ")
