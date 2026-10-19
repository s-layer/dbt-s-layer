"""Rendering a semantic view as a Databricks metric view definition (SEMVW-03)."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from typing import Any

import jinja2
import pytest
from ruamel.yaml import YAML

from dbt_s_layer.databricks import CONFIG, render_databricks, yaml_string
from dbt_s_layer.errors import SemanticViewError
from dbt_s_layer.semantic_views import (
    DerivedMetricSpec,
    DimensionSpec,
    MetricRef,
    MetricSpec,
    NonAdditiveSpec,
    PercentileSpec,
    RelationshipSpec,
    RenderedView,
    SemanticViewSpec,
    TableSpec,
)

ITEMS = TableSpec(alias="order_items", semantic_model="order_items", ref="{{ ref('order_items') }}")
ORDERS = TableSpec(alias="orders", semantic_model="orders", ref="{{ ref('orders') }}")
CUSTOMERS = TableSpec(alias="customers", semantic_model="customers", ref="{{ ref('customers') }}")
ITEMS_TO_ORDERS = RelationshipSpec(
    name="order_items_to_orders",
    child="order_items",
    child_column="order_id",
    parent="orders",
    parent_column="order_id",
    entity="order",
)
ORDERS_TO_CUSTOMERS = RelationshipSpec(
    name="orders_to_customers",
    child="orders",
    child_column="customer_id",
    parent="customers",
    parent_column="id",
    entity="customer",
)
REVENUE = MetricSpec(table="orders", name="revenue", agg="sum", expr="amount")
ORDER_COUNT = MetricSpec(table="orders", name="order_count", agg="count_distinct", expr="order_id")
ORDERED_AT = DimensionSpec(table="orders", name="ordered_at", expr="ordered_at")


def _view(
    *,
    tables: tuple[TableSpec, ...] = (ORDERS,),
    relationships: tuple[RelationshipSpec, ...] = (),
    dimensions: tuple[DimensionSpec, ...] = (ORDERED_AT,),
    metrics: tuple[MetricSpec, ...] = (REVENUE,),
    derived_metrics: tuple[DerivedMetricSpec, ...] = (),
    comment: str | None = None,
) -> SemanticViewSpec:
    return SemanticViewSpec(
        name=tables[0].alias,
        tables=tables,
        relationships=relationships,
        dimensions=dimensions,
        metrics=metrics,
        derived_metrics=derived_metrics,
        comment=comment,
    )


def _render(spec: SemanticViewSpec) -> RenderedView:
    rendered = render_databricks(spec)
    assert rendered.body.startswith(CONFIG + "\n\n")
    return rendered


def _body(spec: SemanticViewSpec) -> str:
    return _render(spec).body.removeprefix(CONFIG + "\n\n")


def _load(body: str) -> Any:
    """The definition Databricks receives: Jinja rendered as dbt would, then YAML."""
    environment = jinja2.Environment(autoescape=False)
    text = environment.from_string(body).render(
        config=lambda **_: "",
        ref=lambda *args, **_: f"`main`.`marts`.`{args[-1]}`",
    )
    return YAML(typ="safe", pure=True).load(StringIO(text))


# --- the definition ------------------------------------------------------------


def test_one_table() -> None:
    assert _body(_view()) == (
        "version: 1.1\n"
        "source: \"{{ ref('orders') }}\"\n"
        "dimensions:\n"
        '  - name: "ordered_at"\n'
        '    expr: "source.ordered_at"\n'
        "measures:\n"
        '  - name: "revenue"\n'
        '    expr: "SUM(source.amount)"\n'
    )


def test_the_definition_is_yaml() -> None:
    loaded = _load(_render(_view(comment="Orders.")).body)
    assert loaded == {
        "version": 1.1,
        "comment": "Orders.",
        "source": "`main`.`marts`.`orders`",
        "dimensions": [{"name": "ordered_at", "expr": "source.ordered_at"}],
        "measures": [{"name": "revenue", "expr": "SUM(source.amount)"}],
    }


def test_comments_and_display_names() -> None:
    spec = _view(
        dimensions=(replace(ORDERED_AT, comment="When.", label="Ordered at"),),
        metrics=(replace(REVENUE, comment="All revenue.", label="Revenue"),),
        comment="Orders.",
    )
    assert _load(_render(spec).body) == {
        "version": 1.1,
        "comment": "Orders.",
        "source": "`main`.`marts`.`orders`",
        "dimensions": [
            {
                "name": "ordered_at",
                "display_name": "Ordered at",
                "comment": "When.",
                "expr": "source.ordered_at",
            }
        ],
        "measures": [
            {
                "name": "revenue",
                "display_name": "Revenue",
                "comment": "All revenue.",
                "expr": "SUM(source.amount)",
            }
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        'say "hi"',
        "back\\slash",
        "two\nlines",
        "tab\there",
        "price in $$",
        "{{ not jinja }} and {% not a tag %}",
        "unicode: café",
    ],
)
def test_text_survives_jinja_and_yaml(text: str) -> None:
    spec = _view(comment=text, dimensions=(replace(ORDERED_AT, comment=text),))
    body = _render(spec).body
    loaded = _load(body)
    assert loaded["comment"] == text
    assert loaded["dimensions"][0]["comment"] == text


def test_a_dollar_pair_cannot_end_the_definition() -> None:
    # dbt-databricks wraps the definition in `$$ ... $$`.
    spec = _view(dimensions=(replace(ORDERED_AT, expr="concat('$$', status)"),))
    body = _render(spec).body
    assert "$" not in body
    assert _load(body)["dimensions"][0]["expr"] == "concat('$$', status)"


def test_yaml_string() -> None:
    assert yaml_string("plain") == '"plain"'
    assert yaml_string('a "b" \\ c\n$') == '"a \\"b\\" \\\\ c\\n\\x24"'


def test_root_exprs() -> None:
    # A plain column is qualified with `source.`, so a column named like a join
    # cannot resolve to the join; any other expr is copied as written.
    spec = _view(
        dimensions=(
            ORDERED_AT,
            DimensionSpec(table="orders", name="month", expr="date_trunc('month', ordered_at)"),
        ),
        metrics=(REVENUE, MetricSpec(table="orders", name="net", agg="sum", expr="amount - tax")),
    )
    loaded = _load(_render(spec).body)
    assert [d["expr"] for d in loaded["dimensions"]] == [
        "source.ordered_at",
        "date_trunc('month', ordered_at)",
    ]
    assert [m["expr"] for m in loaded["measures"]] == ["SUM(source.amount)", "SUM(amount - tax)"]


# --- joins ---------------------------------------------------------------------


def _three_tables() -> SemanticViewSpec:
    return _view(
        tables=(ITEMS, ORDERS, CUSTOMERS),
        relationships=(ITEMS_TO_ORDERS, ORDERS_TO_CUSTOMERS),
        dimensions=(
            DimensionSpec(table="order_items", name="order", expr="order_id"),
            DimensionSpec(table="order_items", name="ordered_at", expr="ordered_at"),
            DimensionSpec(table="orders", name="customer", expr="customer_id"),
            DimensionSpec(table="orders", name="order", expr="order_id"),
            DimensionSpec(table="orders", name="ordered_at", expr="ordered_at"),
            DimensionSpec(table="customers", name="country", expr="country"),
            DimensionSpec(table="customers", name="customer", expr="id"),
        ),
        metrics=(MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),),
    )


def test_joins_nest_by_hop() -> None:
    assert _body(_three_tables()) == (
        "version: 1.1\n"
        "source: \"{{ ref('order_items') }}\"\n"
        "joins:\n"
        '  - name: "orders"\n'
        "    source: \"{{ ref('orders') }}\"\n"
        '    "on": "source.order_id = orders.order_id"\n'
        "    joins:\n"
        '      - name: "customers"\n'
        "        source: \"{{ ref('customers') }}\"\n"
        '        "on": "orders.customer_id = customers.id"\n'
        "dimensions:\n"
        '  - name: "order"\n'
        '    expr: "source.order_id"\n'
        '  - name: "ordered_at"\n'
        '    expr: "source.ordered_at"\n'
        '  - name: "order__customer"\n'
        '    expr: "orders.customer_id"\n'
        '  - name: "order__ordered_at"\n'
        '    expr: "orders.ordered_at"\n'
        '  - name: "order__customer__country"\n'
        '    expr: "orders.customers.country"\n'
        "measures:\n"
        '  - name: "items_sold"\n'
        '    expr: "SUM(source.quantity)"\n'
    )


def test_joined_definition_is_yaml() -> None:
    loaded = _load(_render(_three_tables()).body)
    [orders] = loaded["joins"]
    assert orders["on"] == "source.order_id = orders.order_id"
    assert orders["joins"][0]["name"] == "customers"


def test_a_joined_table_needs_plain_columns() -> None:
    # Every expr of a metric view is in the source's scope, so a joined table's
    # columns would need qualifying inside the expression.
    spec = replace(
        _three_tables(),
        dimensions=(
            DimensionSpec(table="order_items", name="order", expr="order_id"),
            DimensionSpec(table="orders", name="status", expr="upper(status)"),
        ),
    )
    rendered = _render(spec)
    assert "order__status" not in rendered.body
    [warning] = rendered.warnings
    assert "order__status" in warning
    assert "upper(status)" in warning


def test_a_table_named_source_cannot_be_joined() -> None:
    source = TableSpec(alias="source", semantic_model="source", ref="{{ ref('source') }}")
    spec = _view(
        tables=(ORDERS, source),
        relationships=(replace(ORDERS_TO_CUSTOMERS, parent="source", entity="customer"),),
    )
    with pytest.raises(SemanticViewError, match="'source'"):
        render_databricks(spec)


# --- measures ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        ("sum", "SUM(source.amount)"),
        ("min", "MIN(source.amount)"),
        ("max", "MAX(source.amount)"),
        ("average", "AVG(source.amount)"),
        ("count", "COUNT(source.amount)"),
        ("count_distinct", "COUNT(DISTINCT source.amount)"),
        ("sum_boolean", "SUM(CASE WHEN source.amount THEN 1 ELSE 0 END)"),
    ],
)
def test_aggregations(agg: Any, expected: str) -> None:
    spec = _view(metrics=(replace(REVENUE, agg=agg),))
    assert _load(_render(spec).body)["measures"][0]["expr"] == expected


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [
        (
            PercentileSpec(0.5, discrete=False, approximate=False),
            "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY source.amount)",
        ),
        (
            PercentileSpec(0.8999999761581421, discrete=True, approximate=False),
            "PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY source.amount)",
        ),
        (
            PercentileSpec(0.9, discrete=True, approximate=True),
            "APPROX_PERCENTILE(source.amount, 0.9)",
        ),
    ],
)
def test_percentiles(percentile: PercentileSpec, expected: str) -> None:
    spec = _view(metrics=(replace(REVENUE, agg="percentile", percentile=percentile),))
    assert _load(_render(spec).body)["measures"][0]["expr"] == expected


def test_approximate_continuous_percentile_is_refused() -> None:
    # The builder leaves it out for Databricks; the renderer never guesses.
    percentile = PercentileSpec(0.9, discrete=False, approximate=True)
    spec = _view(metrics=(replace(REVENUE, agg="percentile", percentile=percentile),))
    with pytest.raises(SemanticViewError, match="approximate continuous"):
        render_databricks(spec)


@pytest.mark.parametrize(("latest", "semiadditive"), [(True, "last"), (False, "first")])
def test_non_additive_dimension_is_a_window(latest: bool, semiadditive: str) -> None:
    metric = replace(REVENUE, non_additive=NonAdditiveSpec(dimension="ordered_at", latest=latest))
    measure = _load(_render(_view(metrics=(metric,))).body)["measures"][0]
    assert measure["window"] == [
        {"order": "ordered_at", "range": "current", "semiadditive": semiadditive}
    ]


def test_derived_and_ratio_metrics_use_measure() -> None:
    spec = _view(
        metrics=(REVENUE, ORDER_COUNT),
        derived_metrics=(
            DerivedMetricSpec(
                name="aov",
                parts=(
                    MetricRef("orders", "revenue"),
                    " / NULLIF(",
                    MetricRef("orders", "order_count"),
                    ", 0)",
                ),
            ),
            DerivedMetricSpec(name="doubled", parts=(MetricRef("orders", "revenue"), " * 2")),
        ),
    )
    measures = _load(_render(spec).body)["measures"]
    # Simple metrics first, so a derived metric only uses measures defined before it.
    assert [(m["name"], m["expr"]) for m in measures] == [
        ("order_count", "COUNT(DISTINCT source.order_id)"),
        ("revenue", "SUM(source.amount)"),
        ("aov", "MEASURE(`revenue`) / NULLIF(MEASURE(`order_count`), 0)"),
        ("doubled", "MEASURE(`revenue`) * 2"),
    ]


def test_private_metrics() -> None:
    # A metric view has no hidden measures: a private metric another one uses is
    # emitted with a warning, any other private metric is left out.
    hidden = replace(ORDER_COUNT, private=True)
    unused = replace(REVENUE, name="unused", private=True)
    spec = _view(
        metrics=(REVENUE, hidden, unused),
        derived_metrics=(
            DerivedMetricSpec(
                name="aov",
                parts=(MetricRef("orders", "revenue"), " / ", MetricRef("orders", "order_count")),
            ),
            DerivedMetricSpec(
                name="secret", parts=(MetricRef("orders", "revenue"), " * 2"), private=True
            ),
        ),
    )
    rendered = _render(spec)
    names = [m["name"] for m in _load(rendered.body)["measures"]]
    assert names == ["order_count", "revenue", "aov"]
    [warning] = rendered.warnings
    assert "'order_count'" in warning
    assert "private" in warning


# --- names ---------------------------------------------------------------------


def test_names_must_differ_ignoring_case() -> None:
    spec = _view(
        dimensions=(ORDERED_AT, DimensionSpec(table="orders", name="Revenue", expr="status")),
    )
    with pytest.raises(SemanticViewError, match="'revenue'"):
        render_databricks(spec)


def test_a_field_cannot_shadow_a_column_another_expr_uses() -> None:
    amount = DimensionSpec(table="orders", name="amount", expr="amount * 100")
    spec = _view(
        dimensions=(ORDERED_AT, amount),
        metrics=(replace(REVENUE, expr="amount - tax"),),
    )
    with pytest.raises(SemanticViewError, match="amount"):
        render_databricks(spec)


def test_a_field_that_is_its_own_column_is_harmless() -> None:
    amount = DimensionSpec(table="orders", name="amount", expr="amount")
    spec = _view(dimensions=(ORDERED_AT, amount), metrics=(replace(REVENUE, expr="amount - tax"),))
    assert render_databricks(spec).body


def test_an_empty_view_is_refused() -> None:
    with pytest.raises(SemanticViewError, match="at least one"):
        render_databricks(_view(dimensions=(), metrics=()))
