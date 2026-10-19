"""Tests for the adapter-neutral semantic view builder (SEMVW-01, SEMVW-05)."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest

from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import (
    DerivedMetricSpec,
    DimensionSpec,
    MetricRef,
    MetricSpec,
    NonAdditiveSpec,
    PercentileSpec,
    RelationshipSpec,
    SemanticViewBuild,
    SemanticViewSpec,
    build_semantic_views,
)
from tests.conftest import ARTIFACT_SETS, EXPORT_SETS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import MakeProject

Raw = dict[str, Any]


# --- helpers ------------------------------------------------------------------


def _edit(project_dir: Path, file: str, mutate: Callable[[Raw], None]) -> None:
    path = project_dir / "target" / file
    raw: Raw = json.loads(path.read_text())
    mutate(raw)
    path.write_text(json.dumps(raw))


def _semantic(project_dir: Path, mutate: Callable[[Raw], None]) -> None:
    _edit(project_dir, "semantic_manifest.json", mutate)


def _build(project_dir: Path) -> SemanticViewBuild:
    return build_semantic_views(load_project(project_dir))


def _view(build: SemanticViewBuild, name: str) -> SemanticViewSpec:
    return next(v for v in build.views if v.name == name)


def _model(raw: Raw, name: str) -> Raw:
    return next(sm for sm in raw["semantic_models"] if sm["name"] == name)


def _metric(raw: Raw, name: str) -> Raw:
    return next(m for m in raw["metrics"] if m["name"] == name)


def _entity(name: str, type_: str, expr: str | None) -> Raw:
    return {
        "name": name,
        "type": type_,
        "expr": expr,
        "description": None,
        "role": None,
        "label": None,
        "metadata": None,
        "config": None,
    }


def _dimension(name: str, expr: str | None, *, description: str | None = None) -> Raw:
    return {
        "name": name,
        "type": "categorical",
        "expr": expr,
        "description": description,
        "is_partition": False,
        "label": None,
        "metadata": None,
        "config": None,
        "type_params": None,
    }


def _simple(raw: Raw, name: str, model: str, agg: str, expr: str, **params: Any) -> Raw:
    """A new-spec simple metric, shaped like the fixture's own `revenue`."""
    metric = copy.deepcopy(_metric(raw, "revenue"))
    metric["name"] = name
    metric["label"] = name
    metric["type_params"]["expr"] = expr
    agg_params = metric["type_params"]["metric_aggregation_params"]
    agg_params.update(semantic_model=model, agg=agg, expr=expr)
    agg_params.update(params)
    return metric


def _input(name: str, **fields: Any) -> Raw:
    return {
        "name": name,
        "filter": None,
        "alias": None,
        "offset_window": None,
        "offset_to_grain": None,
        **fields,
    }


def _derived(name: str, expr: str, *inputs: Raw) -> Raw:
    return {
        "name": name,
        "label": name,
        "description": None,
        "filter": None,
        "metadata": None,
        "config": None,
        "time_granularity": None,
        "type": "derived",
        "type_params": {"expr": expr, "metrics": list(inputs), "input_measures": []},
    }


def _ratio(name: str, numerator: Raw, denominator: Raw) -> Raw:
    metric = _derived(name, "", numerator)
    metric["type"] = "ratio"
    metric["type_params"] = {
        "numerator": numerator,
        "denominator": denominator,
        "input_measures": [],
    }
    return metric


def _filter(sql: str) -> Raw:
    return {"where_filters": [{"where_sql_template": sql}]}


def _add_semantic_model(project_dir: Path, semantic_model: Raw, node_id: str) -> None:
    """Add a semantic model to both artifacts, reading `node_id`, as `dbt parse` would."""
    name = semantic_model["name"]

    def add_manifest(raw: Raw) -> None:
        raw["semantic_models"][f"semantic_model.fx.{name}"] = {
            "unique_id": f"semantic_model.fx.{name}",
            "name": name,
            "package_name": "fx",
            "model": f"ref('{name}')",
            "depends_on": {"nodes": [node_id]},
        }

    _semantic(project_dir, lambda raw: raw["semantic_models"].append(semantic_model))
    _edit(project_dir, "manifest.json", add_manifest)


def _regions(raw: Raw) -> Raw:
    """A semantic model `regions` (primary entity `region`), copied from orders."""
    regions = copy.deepcopy(_model(raw, "orders"))
    regions["name"] = "regions"
    regions["entities"] = [_entity("region", "primary", "region_id")]
    regions["dimensions"] = [_dimension("region_name", None)]
    return regions


def _warnings(build: SemanticViewBuild, needle: str) -> list[str]:
    return [w for w in build.warnings if needle in w]


# --- views and closure ----------------------------------------------------------


@pytest.mark.parametrize("set_name", [*ARTIFACT_SETS, *EXPORT_SETS])
def test_one_view_per_semantic_model(set_name: str, make_project: MakeProject) -> None:
    project = load_project(make_project(set_name))
    build = build_semantic_views(project)
    expected = sorted(sm.name for sm in project.semantic_manifest.semantic_models)
    assert [v.name for v in build.views] == expected


@pytest.mark.parametrize("set_name", ["fusion_new", "core_new", "core_legacy"])
def test_new_spec_closure(set_name: str, make_project: MakeProject) -> None:
    build = _build(make_project(set_name))
    items = _view(build, "order_items")
    assert [t.alias for t in items.tables] == ["order_items", "orders"]
    assert items.relationships == (
        RelationshipSpec(
            name="order_items_to_orders",
            child="order_items",
            child_column="order_id",
            parent="orders",
            parent_column="order_id",
            entity="order",
        ),
    )
    # orders' foreign entity `customer` matches nothing: customers' entity is `cust`.
    orders = _view(build, "orders")
    assert [t.alias for t in orders.tables] == ["orders"]
    assert orders.relationships == ()


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_two_hop_closure(set_name: str, make_project: MakeProject) -> None:
    items = _view(_build(make_project(set_name)), "order_items")
    assert [t.alias for t in items.tables] == ["order_items", "orders", "customers", "stores"]
    assert [(r.name, r.child_column, r.parent_column) for r in items.relationships] == [
        ("order_items_to_orders", "order_id", "order_id"),
        ("orders_to_customers", "customer_id", "customer_id"),
        ("orders_to_stores", "store_id", "store_id"),
    ]


def test_hop_limit(make_project: MakeProject) -> None:
    # order_items -> orders -> customers -> regions is three hops.
    project_dir = make_project("fusion_new")

    def chain(raw: Raw) -> None:
        _model(raw, "orders")["entities"].append(_entity("cust", "foreign", "customer_id"))
        _model(raw, "customers")["entities"].append(_entity("region", "foreign", "region_id"))

    _semantic(project_dir, chain)
    raw: Raw = json.loads((project_dir / "target" / "semantic_manifest.json").read_text())
    _add_semantic_model(project_dir, _regions(raw), "model.fx.orders")
    build = _build(project_dir)
    assert [t.alias for t in _view(build, "order_items").tables] == [
        "order_items",
        "orders",
        "customers",
    ]
    assert [t.alias for t in _view(build, "orders").tables] == ["orders", "customers", "regions"]


def test_diamond_keeps_the_first_path_and_warns(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_export")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "customers")["entities"].append(
            _entity("store", "foreign", "home_store_id")
        ),
    )
    build = _build(project_dir)
    orders = _view(build, "orders")
    assert [t.alias for t in orders.tables] == ["orders", "customers", "stores"]
    assert [r.name for r in orders.relationships] == ["orders_to_customers", "orders_to_stores"]
    [warning] = _warnings(build, "sem__orders")
    assert "'stores'" in warning
    assert "'orders'" in warning
    assert "'customers'" in warning


def test_path_back_to_the_root_is_ignored(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def cycle(raw: Raw) -> None:
        _model(raw, "orders")["entities"].append(_entity("cust", "foreign", "customer_id"))
        _model(raw, "customers")["entities"].append(_entity("order", "foreign", "last_order_id"))

    _semantic(project_dir, cycle)
    build = _build(project_dir)
    assert [t.alias for t in _view(build, "orders").tables] == ["orders", "customers"]
    assert not _warnings(build, "sem__orders")


def test_foreign_entities_do_not_join_each_other(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "order_items")["entities"].append(
            _entity("customer", "foreign", "customer_id")
        ),
    )
    items = _view(_build(project_dir), "order_items")
    assert [t.alias for t in items.tables] == ["order_items", "orders"]


@pytest.mark.parametrize(
    ("set_name", "refs"),
    [
        (
            "fusion_export",
            {
                "customers": "{{ ref('customers', v=2) }}",
                "order_items": "{{ ref('order_items') }}",
                "orders": "{{ ref('orders') }}",
                "stores": "{{ ref('fx_dep', 'stores') }}",
            },
        ),
        (
            "core_export",
            {
                "customers": "{{ ref('customers', v=1) }}",
                "order_items": "{{ ref('order_items') }}",
                "orders": "{{ ref('orders') }}",
                "stores": "{{ ref('fx_dep', 'stores') }}",
            },
        ),
    ],
)
def test_tables_ref_the_node_they_read(
    set_name: str, refs: dict[str, str], make_project: MakeProject
) -> None:
    items = _view(_build(make_project(set_name)), "order_items")
    assert {t.alias: t.ref for t in items.tables} == refs
    assert {t.alias: t.semantic_model for t in items.tables} == {a: a for a in refs}


def test_keys(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "orders")["entities"].extend(
            [_entity("receipt", "unique", "receipt_id"), _entity("order_code", "unique", None)]
        ),
    )
    orders = _view(_build(project_dir), "orders").tables[0]
    assert orders.primary_key == "order_id"
    assert orders.unique_keys == ("order_code", "receipt_id")


def test_unique_key_equal_to_the_primary_key_is_dropped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "orders")["entities"].append(
            _entity("order_again", "unique", "order_id")
        ),
    )
    assert _view(_build(project_dir), "orders").tables[0].unique_keys == ()


def test_computed_key_is_not_a_key_and_does_not_join(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "orders")["entities"][0].update(expr="cast(order_id as int)"),
    )
    build = _build(project_dir)
    assert _view(build, "orders").tables[0].primary_key is None
    assert [t.alias for t in _view(build, "order_items").tables] == ["order_items"]
    [warning] = _warnings(build, "cast(order_id as int)")
    assert "entity 'order' of semantic model 'orders'" in warning


def test_computed_foreign_key_does_not_join(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _model(raw, "order_items")["entities"][1].update(expr="order_id + 0"),
    )
    build = _build(project_dir)
    assert [t.alias for t in _view(build, "order_items").tables] == ["order_items"]
    assert _warnings(build, "entity 'order' of semantic model 'order_items'")


def test_comments_come_from_descriptions(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def describe(raw: Raw) -> None:
        _model(raw, "orders")["description"] = "One row per order."
        _model(raw, "orders")["dimensions"][0]["description"] = "When it was placed."
        _metric(raw, "revenue")["description"] = "Gross revenue."

    _semantic(project_dir, describe)
    build = _build(project_dir)
    orders = _view(build, "orders")
    assert orders.comment == "One row per order."
    assert orders.tables[0].comment == "One row per order."
    assert next(d for d in orders.dimensions if d.name == "ordered_at").comment == (
        "When it was placed."
    )
    assert orders.metrics[0].comment == "Gross revenue."
    # The fixture writes an empty description, which is no comment.
    assert _view(build, "order_items").comment is None


# --- dimensions -------------------------------------------------------------------


@pytest.mark.parametrize("set_name", ["fusion_new", "core_new", "core_legacy"])
def test_dimensions_include_entities(set_name: str, make_project: MakeProject) -> None:
    items = _view(_build(make_project(set_name)), "order_items")
    assert items.dimensions == (
        DimensionSpec(table="order_items", name="order", expr="order_id"),
        DimensionSpec(table="order_items", name="order_item", expr="order_item_id"),
        DimensionSpec(table="order_items", name="ordered_at", expr="ordered_at"),
        DimensionSpec(table="orders", name="customer", expr="customer_id"),
        DimensionSpec(table="orders", name="order", expr="order_id"),
        DimensionSpec(table="orders", name="ordered_at", expr="ordered_at"),
    )


def test_natural_entities_are_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def scd(raw: Raw) -> None:
        customers = _model(raw, "customers")
        customers["entities"][0]["type"] = "natural"
        customers["primary_entity"] = "customer_scd"
        for name, flag in (("valid_from", "is_start"), ("valid_to", "is_end")):
            dim = copy.deepcopy(customers["dimensions"][0])
            dim.update(name=name, expr=name)
            dim["type_params"]["validity_params"] = {flag: True}
            customers["dimensions"].append(dim)

    _semantic(project_dir, scd)
    build = _build(project_dir)
    customers = _view(build, "customers")
    assert "cust" not in {d.name for d in customers.dimensions}
    assert customers.tables[0].primary_key is None
    [warning] = _warnings(build, "natural entity 'cust'")
    assert "semantic model 'customers'" in warning


# --- simple metrics ---------------------------------------------------------------


@pytest.mark.parametrize("set_name", ["fusion_new", "core_new", "core_legacy"])
def test_simple_metrics_of_the_root_only(set_name: str, make_project: MakeProject) -> None:
    build = _build(make_project(set_name))
    assert _view(build, "orders").metrics == (
        MetricSpec(table="orders", name="revenue", agg="sum", expr="amount"),
    )
    assert _view(build, "order_items").metrics == (
        MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),
    )


@pytest.mark.parametrize(
    ("agg", "expr", "expected_agg", "expected_expr"),
    [
        ("sum", "amount", "sum", "amount"),
        ("min", "amount", "min", "amount"),
        ("max", "amount", "max", "amount"),
        ("average", "amount", "average", "amount"),
        ("count_distinct", "customer_id", "count_distinct", "customer_id"),
        # metricflow rewrites these before dbt-s-layer sees them (04-RESEARCH §3).
        ("count", "order_id", "sum", "CASE WHEN order_id IS NOT NULL THEN 1 ELSE 0 END"),
        ("sum_boolean", "is_paid", "sum", "CASE WHEN is_paid THEN 1 ELSE 0 END"),
    ],
)
def test_aggregations(
    agg: str, expr: str, expected_agg: str, expected_expr: str, make_project: MakeProject
) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(_simple(raw, "agg_metric", "orders", agg, expr)),
    )
    metric = next(m for m in _view(_build(project_dir), "orders").metrics if m.name == "agg_metric")
    assert (metric.agg, metric.expr) == (expected_agg, expected_expr)


@pytest.mark.parametrize(
    ("discrete", "approximate"), [(False, False), (True, False), (False, True)]
)
def test_percentiles(discrete: bool, approximate: bool, make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    params = {
        "agg_params": {
            "percentile": 0.9,
            "use_discrete_percentile": discrete,
            "use_approximate_percentile": approximate,
        }
    }
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(
            _simple(raw, "p90", "orders", "percentile", "amount", **params)
        ),
    )
    metric = next(m for m in _view(_build(project_dir), "orders").metrics if m.name == "p90")
    assert metric.agg == "percentile"
    assert metric.percentile == PercentileSpec(
        value=0.9, discrete=discrete, approximate=approximate
    )


def test_median_is_the_50th_percentile(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(_simple(raw, "agg_metric", "orders", "median", "amount")),
    )
    metric = next(m for m in _view(_build(project_dir), "orders").metrics if m.name == "agg_metric")
    assert metric.agg == "percentile"
    assert metric.percentile == PercentileSpec(value=0.5, discrete=False, approximate=False)


def test_approximate_discrete_percentile_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    params = {
        "agg_params": {
            "percentile": 0.9,
            "use_discrete_percentile": True,
            "use_approximate_percentile": True,
        }
    }
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(
            _simple(raw, "p90", "orders", "percentile", "amount", **params)
        ),
    )
    build = _build(project_dir)
    assert "p90" not in {m.name for m in _view(build, "orders").metrics}
    [warning] = _warnings(build, "'p90'")
    assert "approximate discrete percentile" in warning


def test_supported_percentiles_depend_on_the_adapter(make_project: MakeProject) -> None:
    # Snowflake has no approximate discrete percentile, Databricks no approximate
    # continuous one (metricflow's own Databricks renderer refuses it).
    project_dir = make_project("fusion_new")

    def percentile(name: str, *, discrete: bool) -> Callable[[Raw], None]:
        options = {
            "percentile": 0.9,
            "use_discrete_percentile": discrete,
            "use_approximate_percentile": True,
        }
        return lambda raw: raw["metrics"].append(
            _simple(raw, name, "orders", "percentile", "amount", agg_params=options)
        )

    _semantic(project_dir, percentile("approx_disc", discrete=True))
    _semantic(project_dir, percentile("approx_cont", discrete=False))
    project = load_project(project_dir)
    for adapter, kept, skipped in (
        ("snowflake", "approx_cont", "approx_disc"),
        ("databricks", "approx_disc", "approx_cont"),
    ):
        build = build_semantic_views(project, adapter=adapter)
        names = {m.name for m in _view(build, "orders").metrics}
        assert kept in names
        assert skipped not in names
        [warning] = _warnings(build, f"{skipped!r}")
        assert "approximate" in warning


def test_labels_are_kept(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def label(raw: Raw) -> None:
        dimension = next(
            d for d in _model(raw, "orders")["dimensions"] if d["name"] == "ordered_at"
        )
        dimension["label"] = "Ordered at"

    _semantic(project_dir, label)
    orders = _view(_build(project_dir), "orders")
    assert next(m for m in orders.metrics if m.name == "revenue").label == "Revenue"
    assert next(d for d in orders.dimensions if d.name == "ordered_at").label == "Ordered at"


def test_private_metric(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir, lambda raw: _metric(raw, "revenue")["type_params"].update(is_private=True)
    )
    assert _view(_build(project_dir), "orders").metrics[0].private


@pytest.mark.parametrize(("window_choice", "latest"), [("max", True), ("min", False)])
def test_non_additive_dimension(
    window_choice: str, latest: bool, make_project: MakeProject
) -> None:
    project_dir = make_project("fusion_new")
    nad: Raw = {"name": "ordered_at", "window_choice": window_choice, "window_groupings": []}
    _semantic(
        project_dir,
        lambda raw: _metric(raw, "revenue")["type_params"]["metric_aggregation_params"].update(
            non_additive_dimension=nad
        ),
    )
    metric = _view(_build(project_dir), "orders").metrics[0]
    assert metric.non_additive == NonAdditiveSpec(dimension="ordered_at", latest=latest)


def test_non_additive_with_window_groupings_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    nad = {"name": "ordered_at", "window_choice": "max", "window_groupings": ["customer"]}
    _semantic(
        project_dir,
        lambda raw: _metric(raw, "revenue")["type_params"]["metric_aggregation_params"].update(
            non_additive_dimension=nad
        ),
    )
    build = _build(project_dir)
    assert _view(build, "orders").metrics == ()
    [warning] = _warnings(build, "'revenue'")
    assert "window_groupings" in warning


def test_fill_nulls_with_is_dropped_with_a_warning(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir, lambda raw: _metric(raw, "revenue")["type_params"].update(fill_nulls_with=0)
    )
    build = _build(project_dir)
    assert [m.name for m in _view(build, "orders").metrics] == ["revenue"]
    [warning] = _warnings(build, "'revenue'")
    assert "fill_nulls_with" in warning


def test_metric_filter_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: _metric(raw, "revenue").update(
            filter=_filter("{{ Dimension('order__ordered_at') }} > '2024-01-01'")
        ),
    )
    build = _build(project_dir)
    assert _view(build, "orders").metrics == ()
    [warning] = _warnings(build, "'revenue'")
    assert "filter" in warning


def test_legacy_measure_filter_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("core_legacy")

    def filtered(raw: Raw) -> None:
        measure = _metric(raw, "revenue")["type_params"]["measure"]
        measure["filter"] = _filter("{{ Dimension('order__ordered_at') }} > '2024-01-01'")

    _semantic(project_dir, filtered)
    build = _build(project_dir)
    assert _view(build, "orders").metrics == ()
    assert _warnings(build, "'revenue'")


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_cumulative_metrics_are_skipped(set_name: str, make_project: MakeProject) -> None:
    build = _build(make_project(set_name))
    assert "revenue_cumulative" not in {m.name for v in build.views for m in v.metrics}
    [warning] = _warnings(build, "'revenue_cumulative'")
    assert "cumulative" in warning


def test_conversion_metrics_are_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def conversion(raw: Raw) -> None:
        raw["metrics"] += [
            _simple(raw, "visits", "orders", "count_distinct", "order_id"),
            _simple(raw, "buys", "orders", "count_distinct", "customer_id"),
        ]
        metric = _derived("visit_to_buy", "")
        metric["type"] = "conversion"
        metric["type_params"] = {
            "conversion_type_params": {
                "base_metric": _input("visits"),
                "conversion_metric": _input("buys"),
                "entity": "order",
                "calculation": "conversion_rate",
                "window": None,
                "constant_properties": None,
            },
            "input_measures": [],
        }
        raw["metrics"].append(metric)

    _semantic(project_dir, conversion)
    build = _build(project_dir)
    [warning] = _warnings(build, "'visit_to_buy'")
    assert "conversion" in warning


def test_metric_named_like_a_dimension_is_an_error(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(
            _simple(raw, "ordered_at", "orders", "max", "ordered_at")
        ),
    )
    with pytest.raises(SemanticViewError, match=r"metric 'ordered_at'.*dimension.*'orders'"):
        _build(project_dir)


def test_entity_dimension_gives_way_to_a_metric(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")
    _semantic(
        project_dir,
        lambda raw: raw["metrics"].append(
            _simple(raw, "customer", "orders", "count_distinct", "x")
        ),
    )
    orders = _view(_build(project_dir), "orders")
    assert "customer" not in {d.name for d in orders.dimensions}
    assert "customer" in {m.name for m in orders.metrics}


# --- derived and ratio metrics ------------------------------------------------------


def _with_order_count(raw: Raw) -> None:
    raw["metrics"].append(_simple(raw, "order_count", "orders", "count_distinct", "order_id"))


def test_ratio_metric(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def ratio(raw: Raw) -> None:
        _with_order_count(raw)
        raw["metrics"].append(_ratio("aov", _input("revenue"), _input("order_count")))

    _semantic(project_dir, ratio)
    orders = _view(_build(project_dir), "orders")
    assert orders.derived_metrics == (
        DerivedMetricSpec(
            name="aov",
            parts=(
                MetricRef("orders", "revenue"),
                " / NULLIF(",
                MetricRef("orders", "order_count"),
                ", 0)",
            ),
        ),
    )


def test_derived_metric_with_aliases(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def derived(raw: Raw) -> None:
        _with_order_count(raw)
        raw["metrics"].append(
            _derived(
                "aov_plus",
                "(rev / cnt) + revenue_bonus * 2",
                _input("revenue", alias="rev"),
                _input("order_count", alias="cnt"),
            )
        )

    _semantic(project_dir, derived)
    build = _build(project_dir)
    [metric] = _view(build, "orders").derived_metrics
    assert metric.parts == (
        "(",
        MetricRef("orders", "revenue"),
        " / ",
        MetricRef("orders", "order_count"),
        ") + revenue_bonus * 2",
    )
    assert not _warnings(build, "aov_plus")


@pytest.mark.parametrize(
    ("metric", "reason"),
    [
        (
            _derived("both", "revenue + items_sold", _input("revenue"), _input("items_sold")),
            "more than one semantic model",
        ),
        (
            _derived(
                "lagged",
                "revenue",
                _input("revenue", offset_window={"count": 1, "granularity": "day"}),
            ),
            "offset",
        ),
        (
            _derived(
                "paid",
                "revenue",
                _input("revenue", filter=_filter("{{ Dimension('order__ordered_at') }} > '2024'")),
            ),
            "filter",
        ),
        (
            _ratio("per_item", _input("revenue"), _input("items_sold")),
            "more than one semantic model",
        ),
    ],
)
def test_derived_metrics_that_do_not_fit_are_skipped(
    metric: Raw, reason: str, make_project: MakeProject
) -> None:
    project_dir = make_project("fusion_new")
    _semantic(project_dir, lambda raw: raw["metrics"].append(metric))
    build = _build(project_dir)
    assert all(not v.derived_metrics for v in build.views)
    [warning] = _warnings(build, repr(metric["name"]))
    assert reason in warning


def test_derived_metric_over_a_derived_metric_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def nested(raw: Raw) -> None:
        raw["metrics"].append(_derived("double", "revenue * 2", _input("revenue")))
        raw["metrics"].append(_derived("quad", "double * 2", _input("double")))

    _semantic(project_dir, nested)
    build = _build(project_dir)
    assert [m.name for m in _view(build, "orders").derived_metrics] == ["double"]
    [warning] = _warnings(build, "'quad'")
    assert "'double' is not a simple metric" in warning


def test_derived_metric_over_a_skipped_metric_is_skipped(make_project: MakeProject) -> None:
    project_dir = make_project("fusion_new")

    def over_skipped(raw: Raw) -> None:
        _metric(raw, "revenue")["filter"] = _filter("{{ Dimension('order__ordered_at') }} > '2024'")
        raw["metrics"].append(_derived("double", "revenue * 2", _input("revenue")))

    _semantic(project_dir, over_skipped)
    build = _build(project_dir)
    assert _view(build, "orders").derived_metrics == ()
    assert _warnings(build, "'double'")


# --- warnings ---------------------------------------------------------------------


@pytest.mark.parametrize("set_name", [*ARTIFACT_SETS, *EXPORT_SETS])
def test_warnings_are_sorted_and_unique(set_name: str, make_project: MakeProject) -> None:
    build = _build(make_project(set_name))
    assert list(build.warnings) == sorted(set(build.warnings))


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_plain_fixtures_need_no_warnings(set_name: str, make_project: MakeProject) -> None:
    assert _build(make_project(set_name)).warnings == ()


def test_build_is_deterministic(make_project: MakeProject) -> None:
    project = load_project(make_project("fusion_export"))
    assert build_semantic_views(project) == build_semantic_views(project)
