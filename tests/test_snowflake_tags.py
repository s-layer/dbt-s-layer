"""Tests for the S-LAYER tags and synonyms on Snowflake semantic view members."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from s_layer_semantic_formats import validate_block

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
from dbt_s_layer.snowflake import CONFIG, TAG_HOOKS, TAG_SCHEMA, render_snowflake
from tests.conftest import SNOWFLAKE_SETS

if TYPE_CHECKING:
    from tests.conftest import MakeProject

ORDERS = TableSpec(
    alias="orders", semantic_model="orders", ref="{{ ref('orders') }}", primary_key="order_id"
)
REVENUE = MetricSpec(table="orders", name="revenue", agg="sum", expr="amount")
REGION = DimensionSpec(table="orders", name="region", expr="region")

KIND = "{{ s_layer_tags }}.S_LAYER_KIND"
LABEL = "{{ s_layer_tags }}.S_LAYER_LABEL"
SPEC = "{{ s_layer_tags }}.S_LAYER_SPEC"


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


def _line(body: str, clause: str) -> str:
    return body.split(f"{clause} (\n", 1)[1].split("\n", 1)[0].strip()


def test_untagged_view_is_unchanged() -> None:
    body = render_snowflake(_view())
    assert "S_LAYER" not in body
    assert "s_layer_tags" not in body
    assert body.startswith(CONFIG + "\n\nTABLES")


def test_tagged_view_names_the_tag_schema_once() -> None:
    region = replace(REGION, s_layer=_block({"value": {"kind": "category"}}, "dimension"))
    body = render_snowflake(_view(dimensions=(region,)))
    assert body.startswith(CONFIG + "\n{%- set s_layer_tags = " + TAG_SCHEMA + " %}\n\nTABLES")
    assert TAG_SCHEMA == "var('s_layer_tag_schema', target.database ~ '.' ~ target.schema)"


def test_kind_only_dimension() -> None:
    region = replace(REGION, s_layer=_block({"value": {"kind": "category"}}, "dimension"))
    body = render_snowflake(_view(dimensions=(region,)))
    assert _line(body, "DIMENSIONS") == f"orders.region AS region WITH TAG ({KIND} = 'category')"


def test_every_slot_in_snowflake_order() -> None:
    """Synonyms, then tags, then the comment: the only order Snowflake accepts."""
    block = _block(
        {
            "value": {"kind": "currency", "currency": "USD"},
            "display": {"decimals": {"max": 0}},
            "synonyms": ["sales", "turnover"],
        }
    )
    revenue = replace(REVENUE, label="Revenue", comment="Gross revenue.", s_layer=block)
    body = render_snowflake(_view(metrics=(revenue,)))
    spec = (
        '{"display":{"decimals":[0,0],"grouping":true,"negative":"minus"},'
        '"value":{"currency":"USD"}}'
    )
    assert _line(body, "METRICS") == (
        "orders.revenue AS SUM(amount) WITH SYNONYMS = ('sales', 'turnover') "
        f"WITH TAG ({KIND} = 'currency', {LABEL} = 'Revenue', {SPEC} = '{spec}') "
        "COMMENT = 'Gross revenue.'"
    )


def test_label_without_a_block_is_not_tagged() -> None:
    """Tags are opt-in: only members with an s_layer block get them."""
    body = render_snowflake(_view(metrics=(replace(REVENUE, label="Revenue"),)))
    assert "S_LAYER" not in body


def test_derived_metric_is_tagged() -> None:
    share = DerivedMetricSpec(
        name="share",
        parts=(MetricRef("orders", "revenue"), " / 100"),
        s_layer=_block({"value": {"kind": "percentage"}}),
    )
    body = render_snowflake(_view(derived_metrics=(share,)))
    assert f"share AS orders.revenue / 100 WITH TAG ({KIND} = 'percentage'" in body


def test_tag_values_are_escaped() -> None:
    block = _block(
        {"value": {"kind": "category"}, "x-acme": {"note": "it's {{ not jinja }}"}},
        "dimension",
    )
    region = replace(REGION, label="Région's", s_layer=block)
    line = _line(render_snowflake(_view(dimensions=(region,))), "DIMENSIONS")
    assert f"{LABEL} = 'Région''s'" in line
    assert "it''s" in line
    assert "{{ not jinja }}" not in line


def test_spec_over_the_tag_limit_is_an_error() -> None:
    block = _block({"value": {"kind": "category"}, "x-acme": {"note": "x" * 300}}, "dimension")
    region = replace(REGION, s_layer=block)
    with pytest.raises(SLayerBlockError, match="dimension 'region'.*256"):
        render_snowflake(_view(dimensions=(region,)))


# --- the tag objects ------------------------------------------------------------------


def _with_hooks(project_dir: Path) -> Path:
    path = project_dir / "dbt_project.yml"
    hooks = "".join(f"  - {hook}\n" for hook in TAG_HOOKS)
    path.write_text(path.read_text() + "on-run-start:\n" + hooks)
    return project_dir


@pytest.mark.parametrize("set_name", SNOWFLAKE_SETS)
def test_missing_tag_hook_warns(set_name: str, make_project: MakeProject) -> None:
    result = render_semantic_views(load_project(make_project(set_name)), adapter="snowflake")
    assert any("S_LAYER_KIND" in m.body for m in result.models)
    [warning] = [w for w in result.warnings if "on-run-start" in w]
    assert "S_LAYER_KIND" in warning
    assert "README" in warning


@pytest.mark.parametrize("set_name", SNOWFLAKE_SETS)
def test_tag_hooks_silence_the_warning(set_name: str, make_project: MakeProject) -> None:
    project_dir = _with_hooks(make_project(set_name))
    result = render_semantic_views(load_project(project_dir), adapter="snowflake")
    assert not [w for w in result.warnings if "on-run-start" in w]


def test_no_tags_no_hook_warning(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    result = render_semantic_views(load_project(project_dir), adapter="snowflake")
    assert not [w for w in result.warnings if "on-run-start" in w]


def test_on_run_start_snippet_is_documented() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    for line in TAG_HOOKS:
        assert line in readme
