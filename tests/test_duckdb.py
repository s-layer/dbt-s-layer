"""Rendering a semantic view as a DuckDB `CREATE SEMANTIC VIEW` body (SEMVW-04)."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import jinja2
import pytest

from dbt_s_layer.duckdb_views import CONFIG, qualify, render_duckdb
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
    RenderedView,
    SemanticViewSpec,
    TableSpec,
    build_semantic_views,
)
from tests.conftest import (
    ARTIFACT_SETS,
    DATABRICKS_SETS,
    DUCKDB_SETS,
    EXPORT_SETS,
    SNOWFLAKE_SETS,
    MakeProject,
)

SNAPSHOTS = Path(__file__).parent / "snapshots" / "semantic_views" / "duckdb"
ALL_SETS = (*ARTIFACT_SETS, *EXPORT_SETS, *SNOWFLAKE_SETS, *DATABRICKS_SETS, *DUCKDB_SETS)

ITEMS = TableSpec(
    alias="order_items",
    semantic_model="order_items",
    ref="{{ ref('order_items') }}",
    primary_key="order_item_id",
)
ORDERS = TableSpec(
    alias="orders", semantic_model="orders", ref="{{ ref('orders') }}", primary_key="order_id"
)
CUSTOMERS = TableSpec(
    alias="customers", semantic_model="customers", ref="{{ ref('customers') }}", primary_key="id"
)
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
STATUS = DimensionSpec(table="orders", name="status", expr="status")


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


def _joined(**kwargs: object) -> SemanticViewSpec:
    """Orders joined to customers, so every column needs its table."""
    return _view(
        tables=(ORDERS, CUSTOMERS),
        relationships=(ORDERS_TO_CUSTOMERS,),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def _render(spec: SemanticViewSpec) -> RenderedView:
    rendered = render_duckdb(spec)
    assert rendered.body.startswith(CONFIG + "\n\n")
    return rendered


def _body(spec: SemanticViewSpec) -> str:
    return _render(spec).body.removeprefix(CONFIG + "\n\n")


def _no_config(**_: object) -> str:
    return ""


def _ref(*args: str, **_: object) -> str:
    return f'"memory"."marts"."{args[-1]}"'


def _sql(body: str) -> str:
    """The DDL body DuckDB receives: the Jinja rendered as dbt would."""
    environment = jinja2.Environment(autoescape=False)
    return environment.from_string(body).render(config=_no_config, ref=_ref)


def _line(body: str, start: str) -> str:
    return next(line.strip() for line in body.splitlines() if line.strip().startswith(start))


# --- the body ----------------------------------------------------------------------


def test_one_table() -> None:
    assert _body(_view()) == (
        "TABLES (\n"
        "  orders AS {{ ref('orders') }}\n"
        "    PRIMARY KEY (order_id)\n"
        ")\n"
        "DIMENSIONS (\n"
        "  orders.ordered_at AS orders.ordered_at\n"
        ")\n"
        "METRICS (\n"
        "  orders.revenue AS SUM(orders.amount)\n"
        ")\n"
    )


def test_joined_tables() -> None:
    spec = SemanticViewSpec(
        name="order_items",
        tables=(ITEMS, replace(ORDERS, comment="One row per order. It's the 'fact' table.")),
        relationships=(ITEMS_TO_ORDERS,),
        dimensions=(
            DimensionSpec("order_items", "order", "order_id", entity=True),
            DimensionSpec("order_items", "ordered_at", "ordered_at"),
            # The join's own key: the same entity as order_items.order.
            DimensionSpec("orders", "order", "order_id", entity=True),
            DimensionSpec("orders", "customer", "customer_id", entity=True),
            STATUS,
        ),
        metrics=(MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),),
    )
    rendered = _render(spec)
    assert rendered.warnings == ()
    assert rendered.body.removeprefix(CONFIG + "\n\n") == (
        "TABLES (\n"
        "  order_items AS {{ ref('order_items') }}\n"
        "    PRIMARY KEY (order_item_id),\n"
        "  orders AS {{ ref('orders') }}\n"
        "    PRIMARY KEY (order_id)\n"
        "    COMMENT = 'One row per order. It''s the ''fact'' table.'\n"
        ")\n"
        "RELATIONSHIPS (\n"
        "  order_items_to_orders AS order_items (order_id) REFERENCES orders (order_id)\n"
        ")\n"
        "DIMENSIONS (\n"
        "  order_items.order AS order_items.order_id,\n"
        "  order_items.ordered_at AS order_items.ordered_at,\n"
        "  orders.order__customer AS orders.customer_id,\n"
        "  orders.order__status AS orders.status\n"
        ")\n"
        "METRICS (\n"
        "  order_items.items_sold AS SUM(order_items.quantity)\n"
        ")\n"
    )


def test_two_hops_name_members_after_both_entities() -> None:
    country = DimensionSpec("customers", "country", "country")
    spec = SemanticViewSpec(
        name="order_items",
        tables=(ITEMS, ORDERS, CUSTOMERS),
        relationships=(ITEMS_TO_ORDERS, ORDERS_TO_CUSTOMERS),
        dimensions=(country,),
        metrics=(MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),),
    )
    body = _body(spec)
    assert "customers.order__customer__country AS customers.country" in body
    assert "orders_to_customers AS orders (customer_id) REFERENCES customers (id)" in body


def test_unique_keys_and_no_primary_key() -> None:
    table = replace(ORDERS, primary_key=None, unique_keys=("order_ref", "order_no"))
    body = _body(_view(tables=(table,)))
    assert body.startswith(
        "TABLES (\n"
        "  orders AS {{ ref('orders') }}\n"
        "    UNIQUE (order_ref)\n"
        "    UNIQUE (order_no)\n"
        ")\n"
    )


def test_comments() -> None:
    spec = _view(
        dimensions=(replace(ORDERED_AT, comment="When.", label="Ordered at"),),
        metrics=(replace(REVENUE, comment="All revenue.", label="Revenue"),),
        comment="Orders.",
    )
    body = _body(spec)
    assert "  orders.ordered_at AS orders.ordered_at COMMENT = 'When.'\n" in body
    assert "  orders.revenue AS SUM(orders.amount) COMMENT = 'All revenue.'\n" in body
    assert body.endswith(")\nCOMMENT = 'Orders.'\n")
    # DuckDB semantic views have no labels.
    assert "Ordered at" not in body
    assert "Revenue'" not in body


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("it's", "'it''s'"),
        ("back\\slash", "'back\\slash'"),
        ("two\nlines", "'two\nlines'"),
        ("a ) , -- b /* $$", "'a ) , -- b /* $$'"),
        ("{{ not jinja }} and {% not a tag %}", "'{{ not jinja }} and {% not a tag %}'"),
    ],
)
def test_text_survives_jinja(text: str, literal: str) -> None:
    sql = _sql(_render(_view(comment=text)).body)
    # Jinja drops the final newline.
    assert sql.endswith(f")\nCOMMENT = {literal}")


# --- exprs --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("status", "orders.status"),
        ("upper(status)", "upper(orders.status)"),
        ("date_trunc('month', ordered_at)", "date_trunc('month', orders.ordered_at)"),
        ("ordered_at + INTERVAL 1 DAY", "orders.ordered_at + INTERVAL 1 DAY"),
        ("ordered_at + INTERVAL '1 day'", "orders.ordered_at + INTERVAL '1 day'"),
        ("EXTRACT(year FROM ordered_at)", "EXTRACT(year FROM orders.ordered_at)"),
        ("CAST(amount AS DOUBLE)", "CAST(orders.amount AS DOUBLE)"),
        ("amount::decimal(18, 2)", "orders.amount::decimal(18, 2)"),
        ("amount::double precision", "orders.amount::double precision"),
        ("status COLLATE nocase", "orders.status COLLATE nocase"),
        ("ordered_at > DATE '2024-01-01'", "orders.ordered_at > DATE '2024-01-01'"),
        (
            "CASE WHEN status = 'paid' THEN amount ELSE 0 END",
            "CASE WHEN orders.status = 'paid' THEN orders.amount ELSE 0 END",
        ),
        ('"Status Code"', 'orders."Status Code"'),
        ('lower("status")', 'lower(orders."status")'),
        ("orders.status", "orders.status"),
        ("current_date - ordered_at", "current_date - orders.ordered_at"),
        ("status IS NOT NULL AND amount BETWEEN 1 AND 2", None),
        ("payload->>'$.kind'", "orders.payload->>'$.kind'"),
        ("payload->'kind'", "orders.payload->'kind'"),
        ("round(amount, digits := 2)", "round(orders.amount, digits := 2)"),
        ("note LIKE '%x!%' ESCAPE '!'", "orders.note LIKE '%x!%' ESCAPE '!'"),
        ("coalesce(status, 'none') -- tidy", "coalesce(orders.status, 'none') -- tidy"),
        ("ordered_at + INTERVAL '1' DAY", "orders.ordered_at + INTERVAL '1' DAY"),
        ("ordered_at - INTERVAL (-1) DAY", "orders.ordered_at - INTERVAL (-1) DAY"),
        ("ordered_at + INTERVAL (days) DAY", "orders.ordered_at + INTERVAL (orders.days) DAY"),
        ("ordered_at + INTERVAL 1.5 HOUR", "orders.ordered_at + INTERVAL 1.5 HOUR"),
        ("ordered_at AT TIME ZONE 'UTC'", "orders.ordered_at AT TIME ZONE 'UTC'"),
        ("ordered_at AT TIME ZONE tz", "orders.ordered_at AT TIME ZONE orders.tz"),
        (
            "CAST(ordered_at AS TIMESTAMP WITH TIME ZONE)",
            "CAST(orders.ordered_at AS TIMESTAMP WITH TIME ZONE)",
        ),
        ("ordered_at::timestamp with time zone", "orders.ordered_at::timestamp with time zone"),
        ("time + 1", "orders.time + 1"),
        ("zone || tz", "orders.zone || orders.tz"),
        ("substring(status FROM 1 FOR 3)", "substring(orders.status FROM 1 FOR 3)"),
        (
            "overlay(status PLACING 'x' FROM 1 FOR 2)",
            "overlay(orders.status PLACING 'x' FROM 1 FOR 2)",
        ),
        (
            "ordered_at::timestamp without time zone",
            "orders.ordered_at::timestamp without time zone",
        ),
    ],
)
def test_columns_are_qualified(expr: str, expected: str | None) -> None:
    if expected is None:
        expected = "orders.status IS NOT NULL AND orders.amount BETWEEN 1 AND 2"
    assert qualify(expr, "orders") == expected


@pytest.mark.parametrize(
    "expr",
    [
        "list_transform(tags, x -> upper(x))",
        "list_transform(tags, lambda x: upper(x))",
        "(SELECT max(amount) FROM orders)",
        "[t * 2 FOR t IN tags]",
        "list_sum([t FOR t IN tags IF t > 0])",
        "(FROM other)",
        "amount > (FROM other SELECT max(amount))",
    ],
)
def test_exprs_with_lambdas_or_subqueries_are_not_qualified(expr: str) -> None:
    assert qualify(expr, "orders") is None


def test_a_joined_view_qualifies_every_expr() -> None:
    month = DimensionSpec("orders", "month", "date_trunc('month', ordered_at)")
    country = DimensionSpec("customers", "country", "upper(country)")
    paid = MetricSpec(
        table="orders", name="paid", agg="sum", expr="CASE WHEN status = 'paid' THEN amount END"
    )
    body = _body(_joined(dimensions=(month, country), metrics=(paid,)))
    assert "  orders.month AS date_trunc('month', orders.ordered_at),\n" in body
    assert "  customers.customer__country AS upper(customers.country)\n" in body
    assert "  orders.paid AS SUM(CASE WHEN orders.status = 'paid' THEN orders.amount END)\n" in body


def test_a_one_table_view_keeps_exprs_as_written() -> None:
    # Nothing can collide, so even an expr the scanner cannot read is safe.
    tags = DimensionSpec("orders", "tags", "list_transform(tags, x -> upper(x))")
    month = DimensionSpec("orders", "month", "date_trunc('month', ordered_at)")
    rendered = _render(_view(dimensions=(month, tags)))
    assert rendered.warnings == ()
    assert "  orders.month AS date_trunc('month', ordered_at),\n" in rendered.body
    assert "  orders.tags AS list_transform(tags, x -> upper(x))\n" in rendered.body


def test_a_trailing_line_comment_ends_its_line() -> None:
    status = replace(STATUS, expr="coalesce(status, 'none') -- tidy")
    body = _body(_joined(dimensions=(status, ORDERED_AT)))
    assert "  orders.status AS coalesce(orders.status, 'none') -- tidy\n,\n" in body


def test_members_the_scanner_cannot_read_are_skipped() -> None:
    tags = DimensionSpec("orders", "tags", "list_transform(tags, x -> upper(x))")
    country = DimensionSpec("customers", "country", "(SELECT 1)")
    ordered_at = replace(ORDERED_AT, expr="list_max(list_transform(dates, d -> d))")
    weird = MetricSpec(table="orders", name="weird", agg="max", expr="list_max(xs, lambda x: x)")
    latest = MetricSpec(
        table="orders",
        name="latest",
        agg="sum",
        expr="amount",
        non_additive=NonAdditiveSpec(dimension="ordered_at", latest=True),
    )
    doubled = DerivedMetricSpec(name="doubled", parts=(MetricRef("orders", "weird"), " * 2"))
    rendered = _render(
        _joined(
            dimensions=(tags, ordered_at, STATUS, country),
            metrics=(REVENUE, weird, latest),
            derived_metrics=(doubled,),
        )
    )
    body = rendered.body
    assert "orders.status AS orders.status" in body
    assert "orders.revenue AS SUM(orders.amount)" in body
    for name in ("tags", "ordered_at", "country", "weird", "latest", "doubled"):
        assert f".{name} AS" not in body and f"  {name} AS" not in body
    assert rendered.warnings == (
        "semantic views: sem__orders: skipped dimension 'customer__country' of semantic model "
        "'customers': dbt-s-layer cannot tell which names in its expr are columns ('(SELECT "
        "1)'), and DuckDB needs every column of a multi-table semantic view qualified with its "
        "table.",
        "semantic views: sem__orders: skipped dimension 'ordered_at' of semantic model "
        "'orders': dbt-s-layer cannot tell which names in its expr are columns ('list_max("
        "list_transform(dates, d -> d))'), and DuckDB needs every column of a multi-table "
        "semantic view qualified with its table.",
        "semantic views: sem__orders: skipped dimension 'tags' of semantic model 'orders': "
        "dbt-s-layer cannot tell which names in its expr are columns ('list_transform(tags, "
        "x -> upper(x))'), and DuckDB needs every column of a multi-table semantic view "
        "qualified with its table.",
        "semantic views: sem__orders: skipped metric 'doubled': its input 'weird' was skipped.",
        "semantic views: sem__orders: skipped metric 'latest': its non-additive dimension "
        "'ordered_at' was skipped.",
        "semantic views: sem__orders: skipped metric 'weird' of semantic model 'orders': "
        "dbt-s-layer cannot tell which names in its expr are columns ('list_max(xs, lambda x: "
        "x)'), and DuckDB needs every column of a multi-table semantic view qualified with its "
        "table.",
    )


def test_a_view_left_empty_by_skipping_is_refused() -> None:
    tags = DimensionSpec("orders", "tags", "list_transform(tags, x -> upper(x))")
    with pytest.raises(SemanticViewError, match="at least one dimension or metric"):
        render_duckdb(_joined(dimensions=(tags,), metrics=()))


def test_a_column_that_is_not_a_plain_name_is_quoted() -> None:
    table = replace(ORDERS, primary_key="order id")
    spec = _view(
        tables=(table,),
        dimensions=(replace(STATUS, expr="status$code"), replace(ORDERED_AT, expr="group")),
    )
    body = _body(spec)
    assert '    PRIMARY KEY ("order id")\n' in body
    assert '  orders.status AS orders."status$code"' in body
    assert '  orders.ordered_at AS orders."group"' in body


# --- aggregations -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        ("sum", "SUM(orders.amount)"),
        ("min", "MIN(orders.amount)"),
        ("max", "MAX(orders.amount)"),
        ("average", "AVG(orders.amount)"),
        ("count", "COUNT(orders.amount)"),
        ("count_distinct", "COUNT(DISTINCT orders.amount)"),
        ("sum_boolean", "SUM(CASE WHEN orders.amount THEN 1 ELSE 0 END)"),
    ],
)
def test_aggregations(agg: str, expected: str) -> None:
    metric = replace(REVENUE, agg=agg)  # pyright: ignore[reportArgumentType]
    assert _line(_body(_view(metrics=(metric,))), "orders.revenue AS") == (
        f"orders.revenue AS {expected}"
    )


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [
        (
            PercentileSpec(0.9, discrete=False, approximate=False),
            "PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY orders.amount)",
        ),
        (
            PercentileSpec(0.9, discrete=True, approximate=False),
            "PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY orders.amount)",
        ),
        (
            PercentileSpec(0.8999999761581421, discrete=False, approximate=True),
            "APPROX_QUANTILE(orders.amount, 0.9)",
        ),
    ],
)
def test_percentiles(percentile: PercentileSpec, expected: str) -> None:
    metric = replace(REVENUE, agg="percentile", percentile=percentile)
    assert _line(_body(_view(metrics=(metric,))), "orders.revenue AS") == (
        f"orders.revenue AS {expected}"
    )


def test_approximate_discrete_percentile_is_refused() -> None:
    percentile = PercentileSpec(0.5, discrete=True, approximate=True)
    metric = replace(REVENUE, agg="percentile", percentile=percentile)
    with pytest.raises(SemanticViewError, match="approximate discrete percentile"):
        render_duckdb(_view(metrics=(metric,)))


@pytest.mark.parametrize(("latest", "order"), [(True, "ASC"), (False, "DESC")])
def test_non_additive_dimension(latest: bool, order: str) -> None:
    metric = replace(REVENUE, non_additive=NonAdditiveSpec(dimension="ordered_at", latest=latest))
    assert _line(_body(_view(metrics=(metric,))), "orders.revenue") == (
        f"orders.revenue NON ADDITIVE BY (orders.ordered_at {order}) AS SUM(orders.amount)"
    )


def test_derived_and_ratio_metrics_use_bare_names() -> None:
    ratio = DerivedMetricSpec(
        name="average_order_value",
        parts=(
            MetricRef("orders", "revenue"),
            " / NULLIF(",
            MetricRef("orders", "order_count"),
            ", 0)",
        ),
        comment="AOV.",
    )
    doubled = DerivedMetricSpec(name="doubled", parts=(MetricRef("orders", "revenue"), " * 2"))
    body = _body(_view(metrics=(REVENUE, ORDER_COUNT), derived_metrics=(ratio, doubled)))
    assert body.endswith(
        "METRICS (\n"
        "  orders.revenue AS SUM(orders.amount),\n"
        "  orders.order_count AS COUNT(DISTINCT orders.order_id),\n"
        "  average_order_value AS revenue / NULLIF(order_count, 0) COMMENT = 'AOV.',\n"
        "  doubled AS revenue * 2\n"
        ")\n"
    )


def test_private_metrics() -> None:
    revenue = replace(REVENUE, private=True)
    doubled = DerivedMetricSpec(
        name="doubled", parts=(MetricRef("orders", "revenue"), " * 2"), private=True
    )
    body = _body(_view(metrics=(revenue,), derived_metrics=(doubled,)))
    assert "  PRIVATE orders.revenue AS SUM(orders.amount),\n" in body
    assert "  PRIVATE doubled AS revenue * 2\n" in body


# --- names --------------------------------------------------------------------------


def test_member_names_are_unquoted_even_when_reserved() -> None:
    order = DimensionSpec("orders", "order", "order_id", entity=True)
    select = replace(REVENUE, name="select")
    body = _body(_view(dimensions=(order,), metrics=(select,)))
    assert "  orders.order AS orders.order_id\n" in body
    assert "  orders.select AS SUM(orders.amount)\n" in body


@pytest.mark.parametrize(("name", "shown"), [("as", '"as"'), ("Net Revenue", '"Net Revenue"')])
def test_names_that_need_quotes(name: str, shown: str) -> None:
    metric = replace(REVENUE, name=name)
    doubled = DerivedMetricSpec(name="doubled", parts=(MetricRef("orders", name), " * 2"))
    body = _body(_view(metrics=(metric,), derived_metrics=(doubled,)))
    assert f"  orders.{shown} AS SUM(orders.amount),\n" in body
    assert f"  doubled AS {shown} * 2\n" in body
    derived = DerivedMetricSpec(name=name, parts=(MetricRef("orders", "revenue"), " * 2"))
    body = _body(_view(derived_metrics=(derived,)))
    assert f"  {shown} AS revenue * 2\n" in body


def test_a_reserved_semantic_model_name_gets_a_table_alias() -> None:
    order = TableSpec(alias="order", semantic_model="order", ref="{{ ref('order') }}")
    to_order = replace(ITEMS_TO_ORDERS, parent="order", name="order_items_to_order")
    spec = SemanticViewSpec(
        name="order_items",
        tables=(ITEMS, order),
        relationships=(to_order,),
        dimensions=(DimensionSpec("order", "status", "upper(status)"),),
        metrics=(MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),),
    )
    body = _body(spec)
    assert "  order_t AS {{ ref('order') }}\n" in body
    assert "order_items_to_order AS order_items (order_id) REFERENCES order_t (order_id)" in body
    assert "  order_t.order__status AS upper(order_t.status)\n" in body


def test_a_table_alias_cannot_collide() -> None:
    order = TableSpec(alias="order", semantic_model="order", ref="{{ ref('order') }}")
    order_t = TableSpec(alias="order_t", semantic_model="order_t", ref="{{ ref('order_t') }}")
    spec = SemanticViewSpec(
        name="order",
        tables=(order, order_t),
        relationships=(
            RelationshipSpec("order_to_order_t", "order", "x", "order_t", "x", entity="x"),
        ),
        metrics=(MetricSpec(table="order", name="n", agg="count", expr="x"),),
    )
    with pytest.raises(SemanticViewError, match="'order_t'"):
        render_duckdb(spec)


def test_names_must_differ_ignoring_case() -> None:
    spec = _view(dimensions=(replace(ORDERED_AT, name="Revenue"),))
    with pytest.raises(SemanticViewError, match="case-insensitive"):
        render_duckdb(spec)


def test_a_joined_name_can_clash_with_a_root_name() -> None:
    clash = DimensionSpec("orders", "customer__country", "country")
    country = DimensionSpec("customers", "country", "country")
    with pytest.raises(SemanticViewError, match="'customer__country'"):
        render_duckdb(_joined(dimensions=(clash, country)))


def test_a_name_with_a_reserved_prefix_is_refused() -> None:
    # Two hops through an entity named `source` give `order__Source__country`, which
    # holds the `__SOURCE__` placeholder prefix.
    spec = SemanticViewSpec(
        name="order_items",
        tables=(ITEMS, ORDERS, CUSTOMERS),
        relationships=(ITEMS_TO_ORDERS, replace(ORDERS_TO_CUSTOMERS, entity="Source")),
        dimensions=(DimensionSpec("customers", "country", "country"),),
    )
    with pytest.raises(SemanticViewError, match="order__Source__country"):
        render_duckdb(spec)


def test_an_empty_view_is_refused() -> None:
    with pytest.raises(SemanticViewError, match="at least one dimension or metric"):
        render_duckdb(_view(dimensions=(), metrics=()))


# --- snapshots ----------------------------------------------------------------------


def _rendered(set_name: str, make_project: MakeProject) -> dict[str, RenderedView]:
    project = load_project(make_project(set_name))
    build = build_semantic_views(project, adapter="duckdb")
    return {f"sem__{view.name}": render_duckdb(view) for view in build.views}


@pytest.mark.parametrize("set_name", ALL_SETS)
def test_snapshots_cover_every_view(set_name: str, make_project: MakeProject) -> None:
    rendered = {name: view.body for name, view in _rendered(set_name, make_project).items()}
    folder = SNAPSHOTS / set_name
    if os.environ.get("UPDATE_SNAPSHOTS"):
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.sql"):
            old.unlink()
        for name, text in rendered.items():
            (folder / f"{name}.sql").write_text(text)
    on_disk = {p.stem: p.read_text() for p in sorted(folder.glob("*.sql"))}
    assert on_disk == rendered, "re-run with UPDATE_SNAPSHOTS=1 and review the diff"


def test_snapshots_exist() -> None:
    folders = {p.name for p in SNAPSHOTS.iterdir() if any(p.glob("*.sql"))}
    assert folders == set(ALL_SETS)


# --- the DuckDB fixture projects ----------------------------------------------------


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_duckdb_fixture_metrics(set_name: str, make_project: MakeProject) -> None:
    rendered = _rendered(set_name, make_project)
    orders = rendered["sem__orders"].body
    assert "COMMENT = 'One row per order. It''s the ''fact'' table.'" in orders
    lines = {line.strip() for line in orders.splitlines()}
    assert (
        "orders.order_count AS COUNT(DISTINCT orders.order_id) COMMENT = 'Distinct orders.',"
        in lines
    )
    assert (
        "orders.revenue_p90 AS PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY orders.amount)," in lines
    )
    assert (
        "orders.latest_revenue NON ADDITIVE BY (orders.ordered_at ASC) AS SUM(orders.amount),"
        in lines
    )
    assert "  average_order_value AS revenue / NULLIF(order_count, 0)" in orders
    assert "  revenue_doubled AS revenue * 2" in orders
    assert "WITH SYNONYMS = ('sales', 'turnover')" in orders
    assert "revenue_cumulative" not in orders
    assert "revenue_p50_approx" not in orders
    # order_items and orders both have ordered_at: every column names its table.
    items = rendered["sem__order_items"].body
    assert "  orders.order__order_month AS date_trunc('month', orders.ordered_at)" in items
    assert "  order_items.ordered_at AS order_items.ordered_at" in items
