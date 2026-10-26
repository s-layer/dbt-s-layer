"""Tests for the Snowflake semantic view renderer (SEMVW-02, SEMVW-05)."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dbt_s_layer.errors import ExportRenderError, SemanticViewError
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import (
    Aggregation,
    DerivedMetricSpec,
    DimensionSpec,
    MetricRef,
    MetricSpec,
    NonAdditiveSpec,
    PercentileSpec,
    RelationshipSpec,
    SemanticViewSpec,
    TableSpec,
    build_semantic_views,
)
from dbt_s_layer.snowflake import quote_identifier, render_snowflake, sql_string
from tests.conftest import ARTIFACT_SETS, EXPORT_SETS, SNOWFLAKE_SETS

if TYPE_CHECKING:
    from tests.conftest import MakeProject

SNAPSHOTS = Path(__file__).parent / "snapshots" / "semantic_views"
CONFIG = "{{ config(materialized='semantic_view', static_analysis='off') }}\n\n"

ORDERS = TableSpec(
    alias="orders", semantic_model="orders", ref="{{ ref('orders') }}", primary_key="order_id"
)
ITEMS = TableSpec(
    alias="order_items",
    semantic_model="order_items",
    ref="{{ ref('order_items') }}",
    primary_key="order_item_id",
)
REVENUE = MetricSpec(table="orders", name="revenue", agg="sum", expr="amount")
ORDERED_AT = DimensionSpec(table="orders", name="ordered_at", expr="ordered_at")


def _view(
    *,
    tables: tuple[TableSpec, ...] = (ORDERS,),
    dimensions: tuple[DimensionSpec, ...] = (ORDERED_AT,),
    metrics: tuple[MetricSpec, ...] = (REVENUE,),
    derived_metrics: tuple[DerivedMetricSpec, ...] = (),
    comment: str | None = None,
) -> SemanticViewSpec:
    return SemanticViewSpec(
        name="orders",
        tables=tables,
        dimensions=dimensions,
        metrics=metrics,
        derived_metrics=derived_metrics,
        comment=comment,
    )


def _body(spec: SemanticViewSpec) -> str:
    text = render_snowflake(spec)
    assert text.startswith(CONFIG)
    return text.removeprefix(CONFIG)


def _metric_line(metric: MetricSpec) -> str:
    body = _body(_view(metrics=(metric,)))
    return body.split("METRICS (\n", 1)[1].split("\n)", 1)[0].strip()


# --- clauses --------------------------------------------------------------------


def test_single_table_view() -> None:
    assert _body(_view()) == (
        "TABLES (\n"
        "  orders AS {{ ref('orders') }}\n"
        "    PRIMARY KEY (order_id)\n"
        ")\n"
        "DIMENSIONS (\n"
        "  orders.ordered_at AS ordered_at\n"
        ")\n"
        "METRICS (\n"
        "  orders.revenue AS SUM(amount)\n"
        ")\n"
    )


def test_joined_view_with_every_clause() -> None:
    spec = SemanticViewSpec(
        name="order_items",
        tables=(
            replace(ITEMS, comment="One row per item."),
            replace(ORDERS, unique_keys=("order_code", "receipt_id")),
        ),
        relationships=(
            RelationshipSpec(
                name="order_items_to_orders",
                child="order_items",
                child_column="order_id",
                parent="orders",
                parent_column="order_id",
            ),
        ),
        dimensions=(
            DimensionSpec(table="order_items", name="order", expr="order_id"),
            DimensionSpec(table="orders", name="status", expr="status", comment="Paid or not."),
        ),
        metrics=(MetricSpec(table="order_items", name="items_sold", agg="sum", expr="quantity"),),
        derived_metrics=(
            DerivedMetricSpec(
                name="double_items", parts=(MetricRef("order_items", "items_sold"), " * 2")
            ),
        ),
        comment="Items and their orders.",
    )
    assert _body(spec) == (
        "TABLES (\n"
        "  order_items AS {{ ref('order_items') }}\n"
        "    PRIMARY KEY (order_item_id)\n"
        "    COMMENT = 'One row per item.',\n"
        "  orders AS {{ ref('orders') }}\n"
        "    PRIMARY KEY (order_id)\n"
        "    UNIQUE (order_code)\n"
        "    UNIQUE (receipt_id)\n"
        ")\n"
        "RELATIONSHIPS (\n"
        "  order_items_to_orders AS order_items (order_id) REFERENCES orders (order_id)\n"
        ")\n"
        "DIMENSIONS (\n"
        '  order_items."ORDER" AS order_id,\n'
        "  orders.status AS status COMMENT = 'Paid or not.'\n"
        ")\n"
        "METRICS (\n"
        "  order_items.items_sold AS SUM(quantity),\n"
        "  double_items AS order_items.items_sold * 2\n"
        ")\n"
        "COMMENT = 'Items and their orders.'\n"
    )


def test_table_without_keys() -> None:
    body = _body(_view(tables=(replace(ORDERS, primary_key=None),)))
    assert body.startswith("TABLES (\n  orders AS {{ ref('orders') }}\n)\n")


def test_no_metrics_clause_without_metrics() -> None:
    body = _body(_view(metrics=()))
    assert "METRICS" not in body
    assert body.endswith("DIMENSIONS (\n  orders.ordered_at AS ordered_at\n)\n")


def test_no_dimensions_clause_without_dimensions() -> None:
    body = _body(_view(dimensions=()))
    assert "DIMENSIONS" not in body


def test_derived_metrics_alone_make_a_metrics_clause() -> None:
    derived = DerivedMetricSpec(name="twice", parts=(MetricRef("orders", "revenue"), " * 2"))
    body = _body(_view(metrics=(replace(REVENUE, private=True),), derived_metrics=(derived,)))
    assert "  PRIVATE orders.revenue AS SUM(amount),\n  twice AS orders.revenue * 2\n" in body


# --- metrics ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agg", "sql"),
    [
        ("sum", "SUM(amount)"),
        ("min", "MIN(amount)"),
        ("max", "MAX(amount)"),
        ("average", "AVG(amount)"),
        ("count", "COUNT(amount)"),
        ("count_distinct", "COUNT(DISTINCT amount)"),
        ("sum_boolean", "SUM(CASE WHEN amount THEN 1 ELSE 0 END)"),
    ],
)
def test_aggregations(agg: Aggregation, sql: str) -> None:
    assert _metric_line(replace(REVENUE, agg=agg)) == f"orders.revenue AS {sql}"


@pytest.mark.parametrize(
    ("discrete", "approximate", "sql"),
    [
        (False, False, "PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY amount)"),
        (True, False, "PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY amount)"),
        (False, True, "APPROX_PERCENTILE(amount, 0.9)"),
    ],
)
def test_percentiles(discrete: bool, approximate: bool, sql: str) -> None:
    metric = replace(
        REVENUE,
        agg="percentile",
        percentile=PercentileSpec(value=0.9, discrete=discrete, approximate=approximate),
    )
    assert _metric_line(metric) == f"orders.revenue AS {sql}"


def test_approximate_discrete_percentile_is_refused() -> None:
    metric = replace(
        REVENUE,
        agg="percentile",
        percentile=PercentileSpec(value=0.9, discrete=True, approximate=True),
    )
    with pytest.raises(SemanticViewError, match="approximate discrete"):
        render_snowflake(_view(metrics=(metric,)))


def test_percentile_needs_its_parameters() -> None:
    with pytest.raises(SemanticViewError, match="percentile"):
        render_snowflake(_view(metrics=(replace(REVENUE, agg="percentile"),)))


def test_private_metric() -> None:
    assert _metric_line(replace(REVENUE, private=True)) == "PRIVATE orders.revenue AS SUM(amount)"


@pytest.mark.parametrize(("latest", "order"), [(True, "ASC"), (False, "DESC")])
def test_non_additive(latest: bool, order: str) -> None:
    metric = replace(REVENUE, non_additive=NonAdditiveSpec(dimension="ordered_at", latest=latest))
    assert _metric_line(metric) == (
        f"orders.revenue NON ADDITIVE BY (orders.ordered_at {order}) AS SUM(amount)"
    )


def test_ratio_metric() -> None:
    derived = DerivedMetricSpec(
        name="aov",
        parts=(
            MetricRef("orders", "revenue"),
            " / NULLIF(",
            MetricRef("orders", "n_orders"),
            ", 0)",
        ),
        comment="Average order value.",
    )
    body = _body(_view(derived_metrics=(derived,)))
    assert (
        "  aov AS orders.revenue / NULLIF(orders.n_orders, 0) COMMENT = 'Average order value.'\n"
    ) in body


# --- trailing line comments (review round 1) ----------------------------------------
# An expr is pasted inline, so a trailing `-- comment` would swallow what follows it on
# the line: the `,` between elements, the closing paren of an aggregate, the COMMENT.


def test_dimension_ending_in_a_line_comment_is_terminated() -> None:
    big = DimensionSpec(
        table="orders", name="big", expr="amount > 100 -- big orders", comment="Big."
    )
    body = _body(_view(dimensions=(big, ORDERED_AT)))
    assert "  orders.big AS amount > 100 -- big orders\n COMMENT = 'Big.',\n" in body


def test_dimension_ending_in_a_line_comment_keeps_its_comma() -> None:
    big = DimensionSpec(table="orders", name="big", expr="amount > 100 -- big orders")
    body = _body(_view(dimensions=(big, ORDERED_AT)))
    assert "  orders.big AS amount > 100 -- big orders\n,\n  orders.ordered_at" in body


@pytest.mark.parametrize(
    ("agg", "sql"),
    [
        ("sum", "SUM(amount -- gross\n)"),
        ("count_distinct", "COUNT(DISTINCT amount -- gross\n)"),
        ("sum_boolean", "SUM(CASE WHEN amount -- gross\n THEN 1 ELSE 0 END)"),
    ],
)
def test_aggregate_over_a_line_comment_is_terminated(agg: Aggregation, sql: str) -> None:
    metric = replace(REVENUE, agg=agg, expr="amount -- gross")
    assert f"  orders.revenue AS {sql}\n)" in _body(_view(metrics=(metric,)))


@pytest.mark.parametrize(
    ("discrete", "approximate", "sql"),
    [
        (False, False, "PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY amount -- gross\n)"),
        (False, True, "APPROX_PERCENTILE(amount -- gross\n, 0.9)"),
    ],
)
def test_percentile_over_a_line_comment_is_terminated(
    discrete: bool, approximate: bool, sql: str
) -> None:
    metric = replace(
        REVENUE,
        expr="amount -- gross",
        agg="percentile",
        percentile=PercentileSpec(value=0.9, discrete=discrete, approximate=approximate),
    )
    assert f"  orders.revenue AS {sql}\n)" in _body(_view(metrics=(metric,)))


def test_derived_metric_ending_in_a_line_comment_is_terminated() -> None:
    derived = DerivedMetricSpec(
        name="twice",
        parts=(MetricRef("orders", "revenue"), " * 2 -- doubled"),
        comment="Twice.",
    )
    body = _body(_view(derived_metrics=(derived,)))
    assert "  twice AS orders.revenue * 2 -- doubled\n COMMENT = 'Twice.'\n)" in body


@pytest.mark.parametrize(
    "expr",
    [
        "amount /* gross */",  # a block comment ends itself
        "CASE WHEN note = '-- x' THEN 1 END",  # `--` in a string literal
        'CASE WHEN "a--b" THEN 1 END',  # `--` in a quoted identifier
        "amount -- gross\n",  # already ends the line
        "amount - -1",  # two minus signs, not a comment
    ],
)
def test_exprs_without_a_trailing_line_comment_are_unchanged(expr: str) -> None:
    dimension = DimensionSpec(table="orders", name="x", expr=expr, comment="X.")
    body = _body(_view(dimensions=(dimension,)))
    assert f"  orders.x AS {expr} COMMENT = 'X.'\n" in body
    metric_body = _body(_view(metrics=(replace(REVENUE, expr=expr),)))
    assert f"  orders.revenue AS SUM({expr})\n)" in metric_body


# --- identifiers and literals ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("order", '"ORDER"'),
        ("group", '"GROUP"'),
        ("select", '"SELECT"'),
        ("comment", '"COMMENT"'),
        ("dimensions", '"DIMENSIONS"'),
        ("orders", "orders"),
        ("ordered_at", "ordered_at"),
    ],
)
def test_quote_identifier(name: str, quoted: str) -> None:
    assert quote_identifier(name) == quoted


def test_reserved_names_are_quoted_everywhere() -> None:
    table = TableSpec(alias="order", semantic_model="order", ref="{{ ref('order') }}")
    spec = SemanticViewSpec(
        name="order",
        tables=(table,),
        dimensions=(DimensionSpec(table="order", name="group", expr="group_name"),),
        metrics=(MetricSpec(table="order", name="select", agg="sum", expr="amount"),),
        derived_metrics=(
            DerivedMetricSpec(name="values", parts=(MetricRef("order", "select"), " * 2")),
        ),
    )
    body = _body(spec)
    assert "  \"ORDER\" AS {{ ref('order') }}\n" in body
    assert '  "ORDER"."GROUP" AS group_name\n' in body
    assert '  "ORDER"."SELECT" AS SUM(amount),\n' in body
    assert '  "VALUES" AS "ORDER"."SELECT" * 2\n' in body


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("plain", "'plain'"),
        ("it's", "'it''s'"),
        ("back\\slash", "'back\\\\slash'"),
        ("two\nlines", "'two\\nlines'"),
        ("crlf\r\nend", "'crlf\\nend'"),
    ],
)
def test_sql_string(text: str, literal: str) -> None:
    assert sql_string(text) == literal


def test_jinja_in_comments_and_exprs_is_escaped() -> None:
    spec = _view(
        dimensions=(
            DimensionSpec(
                table="orders", name="status", expr="'{{ x }}'", comment="{% if %} {# c #} {{ y }}"
            ),
        ),
        comment="Orders {{ doc('orders') }}",
    )
    body = _body(spec)
    # dbt renders each `{{ '{{' }}` back to `{{`, so the DDL keeps the user's text.
    assert "{{ '{{' }} x }}" in body
    assert "{{ '{%' }} if %} {{ '{#' }} c #} {{ '{{' }} y }}" in body
    assert "COMMENT = 'Orders {{ '{{' }} doc(''orders'') }}'\n" in body
    # The ref is left alone.
    assert "{{ ref('orders') }}" in body


def test_reserved_prefix_is_refused() -> None:
    spec = _view(dimensions=(DimensionSpec(table="orders", name="x", expr="__REF__.n0_orders"),))
    with pytest.raises(ExportRenderError, match="sem__orders"):
        render_snowflake(spec)


# --- name shadowing ------------------------------------------------------------------


def test_dimension_shadowing_a_metric_column_is_refused() -> None:
    amount = DimensionSpec(table="orders", name="amount", expr="amount * 100")
    with pytest.raises(
        SemanticViewError,
        match=r"orders\.amount.*'amount \* 100'.*orders\.revenue.*'SUM\(amount\)'",
    ):
        render_snowflake(_view(dimensions=(amount,)))


@pytest.mark.parametrize("expr", ["amount", "AMOUNT", "orders.amount", " amount "])
def test_element_that_is_its_column_does_not_shadow(expr: str) -> None:
    amount = DimensionSpec(table="orders", name="amount", expr=expr)
    render_snowflake(_view(dimensions=(amount,)))


def test_metric_shadowing_a_dimension_column_is_refused() -> None:
    status = DimensionSpec(table="orders", name="status", expr="lower(status_code)")
    code = MetricSpec(table="orders", name="status_code", agg="max", expr="status_code")
    with pytest.raises(SemanticViewError, match=r"orders\.status_code"):
        render_snowflake(_view(dimensions=(status,), metrics=(code,)))


def test_an_element_may_use_its_own_name() -> None:
    status = DimensionSpec(table="orders", name="status", expr="lower(status)")
    render_snowflake(_view(dimensions=(status,)))


@pytest.mark.parametrize(
    "metric_expr",
    [
        "CASE WHEN status = 'amount' THEN 1 END",  # a string literal
        'SUM("amount")',  # a quoted identifier: case-sensitive, not the element
        "amount(status)",  # a function name
        "other.amount",  # another table's column
        "SUM(x) -- amount",  # a comment
    ],
)
def test_what_is_not_a_reference(metric_expr: str) -> None:
    amount = DimensionSpec(table="orders", name="amount", expr="amount * 100")
    metric = MetricSpec(table="orders", name="revenue", agg="sum", expr=metric_expr)
    render_snowflake(_view(dimensions=(amount,), metrics=(metric,)))


def test_reserved_words_are_not_references() -> None:
    # An element named `end` cannot be what `CASE ... END` refers to.
    end = DimensionSpec(table="orders", name="end", expr="end_date")
    metric = MetricSpec(
        table="orders", name="revenue", agg="sum", expr="CASE WHEN paid THEN amount END"
    )
    render_snowflake(_view(dimensions=(end,), metrics=(metric,)))


# Type names and date parts are not column references (review round 1).


@pytest.mark.parametrize(
    ("name", "other_expr"),
    [
        ("date", "DATE_TRUNC(month, ordered_at::date)"),
        ("date", "ordered_at :: date"),
        ("date", "CAST(ordered_at AS date)"),
        ("timestamp", "TRY_CAST(ordered_at AS timestamp)"),
        ("varchar", "ordered_at::varchar(10)"),
        ("precision", "amount::double precision"),
        ("int", "CAST(amount AS int) + 1"),
    ],
)
def test_type_names_are_not_references(name: str, other_expr: str) -> None:
    element = DimensionSpec(table="orders", name=name, expr="ordered_at::date")
    other = DimensionSpec(table="orders", name="other", expr=other_expr)
    render_snowflake(_view(dimensions=(element, other)))


@pytest.mark.parametrize(
    ("name", "other_expr"),
    [
        ("month", "DATE_TRUNC(month, ordered_at)"),
        ("month", "DATE_TRUNC( MONTH , ordered_at)"),
        ("day", "DATEADD(day, 1, ordered_at)"),
        ("day", "DATEDIFF(day, ordered_at, shipped_at)"),
        ("hour", "TIMESTAMPADD(hour, 1, ordered_at)"),
        ("quarter", "DATE_PART(quarter, ordered_at)"),
        ("year", "EXTRACT(year FROM ordered_at)"),
        ("week", "LAST_DAY(ordered_at, week)"),
        ("week", "DATE_TRUNC(week, DATEADD(day, 1, ordered_at))"),
    ],
)
def test_date_parts_are_not_references(name: str, other_expr: str) -> None:
    element = DimensionSpec(table="orders", name=name, expr="MONTH(ordered_at)")
    other = DimensionSpec(table="orders", name="other", expr=other_expr)
    render_snowflake(_view(dimensions=(element, other)))


@pytest.mark.parametrize(
    ("name", "other_expr"),
    [
        ("date", "ordered_at > DATE '2024-01-01'"),
        ("timestamp", "TIMESTAMP '2024-01-01 00:00:00' < ordered_at"),
        ("first", "ARRAY_AGG(status) WITHIN GROUP (ORDER BY ordered_at NULLS FIRST)"),
    ],
)
def test_typed_literals_and_nulls_order_are_not_references(name: str, other_expr: str) -> None:
    element = DimensionSpec(table="orders", name=name, expr="ordered_at::date")
    other = DimensionSpec(table="orders", name="other", expr=other_expr)
    render_snowflake(_view(dimensions=(element, other)))


@pytest.mark.parametrize(
    "metric_expr",
    [
        "year",  # a column, not a date part
        "DATEADD(day, year, ordered_at)",  # the amount argument is an expression
        "EXTRACT(month FROM year)",  # so is EXTRACT's operand
        "CAST(year AS int)",  # and CAST's
    ],
)
def test_a_column_named_like_a_date_part_still_shadows(metric_expr: str) -> None:
    year = DimensionSpec(table="orders", name="year", expr="EXTRACT(year FROM ordered_at)")
    metric = MetricSpec(table="orders", name="revenue", agg="max", expr=metric_expr)
    with pytest.raises(SemanticViewError, match=r"orders\.year"):
        render_snowflake(_view(dimensions=(year,), metrics=(metric,)))


def test_type_names_and_date_parts_are_not_quoted() -> None:
    for word in ("date", "timestamp", "int", "month", "year", "week"):
        assert quote_identifier(word) == word


def test_other_tables_do_not_shadow() -> None:
    spec = _view(
        tables=(ORDERS, ITEMS),
        dimensions=(DimensionSpec(table="order_items", name="amount", expr="price * qty"),),
    )
    render_snowflake(spec)


def test_qualified_reference_to_the_same_table_counts() -> None:
    amount = DimensionSpec(table="orders", name="amount", expr="amount * 100")
    metric = MetricSpec(table="orders", name="revenue", agg="sum", expr="orders.amount")
    with pytest.raises(SemanticViewError, match="orders.amount"):
        render_snowflake(_view(dimensions=(amount,), metrics=(metric,)))


# --- snapshots --------------------------------------------------------------------------


def _snapshot_sets() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for set_name in [*ARTIFACT_SETS, *EXPORT_SETS, *SNOWFLAKE_SETS]:
        folder = SNAPSHOTS / set_name
        names = sorted(p.stem for p in folder.glob("*.sql")) if folder.is_dir() else []
        cases += [(set_name, name) for name in names]
    return cases


@pytest.mark.parametrize("set_name", [*ARTIFACT_SETS, *EXPORT_SETS, *SNOWFLAKE_SETS])
def test_snapshots_cover_every_view(set_name: str, make_project: MakeProject) -> None:
    build = build_semantic_views(load_project(make_project(set_name)))
    folder = SNAPSHOTS / set_name
    rendered = {f"sem__{view.name}": render_snowflake(view) for view in build.views}
    if os.environ.get("UPDATE_SNAPSHOTS"):
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.sql"):
            old.unlink()
        for name, text in rendered.items():
            (folder / f"{name}.sql").write_text(text)
    on_disk = {p.stem: p.read_text() for p in sorted(folder.glob("*.sql"))}
    assert on_disk == rendered, "re-run with UPDATE_SNAPSHOTS=1 and review the diff"


def test_snapshots_exist() -> None:
    assert {s for s, _ in _snapshot_sets()} == {*ARTIFACT_SETS, *EXPORT_SETS, *SNOWFLAKE_SETS}


def test_percentile_float32_noise_is_rounded() -> None:
    # Fusion 2.0.8 writes `percentile: 0.9` as 0.8999999761581421.
    metric = replace(
        REVENUE,
        agg="percentile",
        percentile=PercentileSpec(value=0.8999999761581421, discrete=False, approximate=False),
    )
    assert _metric_line(metric) == (
        "orders.revenue AS PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY amount)"
    )


# --- the Snowflake fixture project ------------------------------------------------------


@pytest.mark.parametrize("set_name", SNOWFLAKE_SETS)
def test_snowflake_fixture_metrics(set_name: str, make_project: MakeProject) -> None:
    project = load_project(make_project(set_name))
    build = build_semantic_views(project)
    orders = next(v for v in build.views if v.name == "orders")
    body = render_snowflake(orders)
    assert "  orders.order_count AS COUNT(DISTINCT order_id) COMMENT = 'Distinct orders.'" in body
    assert "  orders.latest_revenue NON ADDITIVE BY (orders.ordered_at ASC) AS SUM(amount)" in body
    assert "  orders.revenue_p90 AS PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY amount)" in body
    assert "  average_order_value AS orders.revenue / NULLIF(orders.order_count, 0)" in body
    assert "  revenue_doubled AS orders.revenue * 2" in body
    assert "COMMENT = 'One row per order. It''s the ''fact'' table.'" in body
    skipped = [w for w in build.warnings if "skipped metric" in w]
    assert [w.split("'")[1] for w in skipped] == ["revenue_cumulative", "revenue_per_item"]
