"""
The generated DuckDB semantic views run: CREATE each one, then query every member.

The DuckDB fixture's models are built from their `raw_code` in an in-memory
DuckDB, under the relation names dbt gave them, with the `semantic_views`
extension from the community repository. Every view rendered from the
`core_duckdb` and `fusion_duckdb` artifacts is created and queried: all its
dimensions together (which joins every table, so an unqualified column shared by
two tables fails), then each public metric with and without them. A column the
generator qualifies wrongly passes CREATE and fails only here (07-RESEARCH §4).

The extension that loads depends on the installed duckdb: 0.16 on 2.0, 0.13 on
1.5.6, 0.11 on 1.5.4 (the DuckDB inside dbt Fusion). CI runs this file on each,
with DBT_S_LAYER_DUCKDB_VERSION naming the duckdb it expects to have installed.
Without network access the extension cannot be installed and the tests skip,
unless DBT_S_LAYER_DUCKDB_EXTENSION=required, as in CI. DBT_S_LAYER_DUCKDB_EXTENSION_PATH
loads a local build instead (unsigned).

Members with an `s_layer` block get `WITH TAG`, which needs extension 0.17. The views
are compiled with `s_layer_duckdb_tags` set to whether the loaded extension takes a
TAG clause; when it does, every block's tags are read back from DESCRIBE.
DBT_S_LAYER_DUCKDB_TAGS=required fails the run when it does not.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import TYPE_CHECKING, Any

import jinja2
import pytest

from dbt_s_layer.duckdb_views import render_duckdb
from dbt_s_layer.exports import render_exports
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import build_semantic_views
from tests.conftest import ARTIFACTS, DUCKDB_SETS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.conftest import MakeProject

duckdb = pytest.importorskip("duckdb")

# `  [PRIVATE ]<alias>.<name> ...` or `  [PRIVATE ]<name> AS ...` (a derived metric).
# `NAME = 'value'` in a TAG clause; `''` is a quote.
_TAG_RE = re.compile(r"(\w+) = '((?:[^']|'')*)'")
# Members with an `s_layer` block a view leaves out: a joined table's key entity.
SKIPPED_TAGGED = {"order_items": 1}
# Rows in each view's own table in the DuckDB fixture.
BASE_ROWS = {"customers": 3, "orders": 6, "order_items": 6, "stores": 2}
_MEMBER_RE = re.compile(r"^  (PRIVATE )?(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\b")


@pytest.fixture(scope="module")
def connection() -> Iterator[Any]:
    expected = os.environ.get("DBT_S_LAYER_DUCKDB_VERSION")
    if expected is not None:
        assert duckdb.__version__ == expected
    path = os.environ.get("DBT_S_LAYER_DUCKDB_EXTENSION_PATH")
    con = duckdb.connect(config={"allow_unsigned_extensions": "true"} if path else {})
    try:
        if path:
            con.execute(f"LOAD '{path}'")
        else:
            con.execute("INSTALL semantic_views FROM community")
            con.execute("LOAD semantic_views")
    except duckdb.Error as exc:
        if os.environ.get("DBT_S_LAYER_DUCKDB_EXTENSION") == "required":
            raise
        pytest.skip(f"cannot load the semantic_views extension: {exc}")
    yield con
    con.close()


@pytest.fixture(scope="module")
def tags(connection: Any) -> bool:
    """Whether the loaded extension takes a TAG clause (0.17+)."""
    con = connection
    con.execute("CREATE OR REPLACE TABLE _tag_probe AS SELECT 1 AS id")
    try:
        con.execute(
            "CREATE OR REPLACE SEMANTIC VIEW _tag_probe_v AS TABLES (t AS _tag_probe "
            "PRIMARY KEY (id)) DIMENSIONS (t.id AS t.id WITH TAG (S_LAYER_KIND = 'identifier'))"
        )
    except duckdb.Error:
        supported = False
    else:
        # Before 0.17 the clause is no error: it is kept as part of the expression.
        supported = bool(_described_tags(con, "_tag_probe_v"))
        con.execute("DROP SEMANTIC VIEW _tag_probe_v")
    con.execute("DROP TABLE _tag_probe")
    if os.environ.get("DBT_S_LAYER_DUCKDB_TAGS") == "required":
        assert supported, "the loaded semantic_views extension has no TAG clause"
    return supported


def _build_models(con: Any, set_name: str) -> dict[tuple[str, Any], str]:
    """Create each fixture model as a table; return (name, version) -> relation name."""
    manifest = json.loads((ARTIFACTS / set_name / "manifest.json").read_text())
    relations: dict[tuple[str, Any], str] = {}
    for node in manifest["nodes"].values():
        relation = node["relation_name"]
        database, schema, _ = relation.split(".")
        con.execute(f"ATTACH IF NOT EXISTS ':memory:' AS {database}")
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
        con.execute(f"CREATE OR REPLACE TABLE {relation} AS {node['raw_code']}")
        relations[(node["name"], node["version"])] = relation
    return relations


def _sql(body: str, relations: dict[tuple[str, Any], str], *, tags: bool) -> str:
    def ref(*args: str, v: Any = None, version: Any = None) -> str:
        return relations[(args[-1], v if v is not None else version)]

    def config(**_: object) -> str:
        return ""

    def var(name: str, default: object = None) -> object:
        assert name == "s_layer_duckdb_tags"
        return tags

    env = jinja2.Environment(autoescape=False)
    return env.from_string(body).render(ref=ref, config=config, var=var)


def _written_tags(sql: str) -> dict[str, dict[str, str]]:
    """Member name -> the tags its line of the compiled body writes."""
    written: dict[str, dict[str, str]] = {}
    for line in sql.splitlines():
        match = _MEMBER_RE.match(line)
        if match is None or " WITH TAG (" not in line:
            continue
        clause = line.split(" WITH TAG (", 1)[1]
        written[match.group(2)] = {
            name: value.replace("''", "'") for name, value in _TAG_RE.findall(clause)
        }
    return written


def _described_tags(con: Any, view: str) -> dict[str, dict[str, str]]:
    """Member name -> the tags DESCRIBE reports, for every tagged member."""
    rows = con.execute(
        f"SELECT object_name, property_value FROM describe_semantic_view('{view}') "
        "WHERE property = 'TAGS' AND object_kind <> 'TABLE'"
    ).fetchall()
    return {name: json.loads(value) for name, value in rows}


def _members(body: str) -> tuple[list[str], list[str]]:
    """The dimensions, and the public metrics, a rendered body declares."""
    sections: dict[str, list[str]] = {"DIMENSIONS": [], "METRICS": []}
    current: list[str] | None = None
    for line in body.splitlines():
        if line.endswith(" ("):
            current = sections.get(line.removesuffix(" ("))
            continue
        match = _MEMBER_RE.match(line)
        if current is not None and match is not None and match.group(1) is None:
            current.append(match.group(2))
    return sections["DIMENSIONS"], sections["METRICS"]


def _query(con: Any, view: str, dimensions: list[str], metrics: list[str]) -> list[Any]:
    args = [f"'{view}'"]
    if dimensions:
        args.append(f"dimensions := {dimensions!r}")
    if metrics:
        args.append(f"metrics := {metrics!r}")
    return con.execute(f"SELECT * FROM semantic_view({', '.join(args)})").fetchall()


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_every_member_of_every_view_answers(
    set_name: str, connection: Any, tags: bool, make_project: MakeProject
) -> None:
    con = connection
    relations = _build_models(con, set_name)
    project = load_project(make_project(set_name))
    views = build_semantic_views(project, adapter="duckdb").views
    assert {v.name for v in views} == {"customers", "order_items", "orders", "stores"}
    for view in views:
        body = render_duckdb(view).body
        name = f"sem__{set_name}_{view.name}"
        sql = _sql(body, relations, tags=tags)
        con.execute(f"CREATE OR REPLACE SEMANTIC VIEW {name} AS {sql}")
        if tags:
            written = _written_tags(sql)
            assert written == _described_tags(con, name), view.name
            assert len(written) == sum(
                m.s_layer is not None
                for m in (*view.dimensions, *view.metrics, *view.derived_metrics)
            ) - SKIPPED_TAGGED.get(view.name, 0), view.name
        dimensions, metrics = _members(body)
        assert dimensions or metrics, view.name
        if dimensions:
            # Every dimension, the key included: one row per row of the view's own table, so
            # a join that drops or fans out rows changes the count.
            assert len(_query(con, name, dimensions, [])) == BASE_ROWS[view.name], view.name
        for metric in metrics:
            assert _query(con, name, [], [metric]), (view.name, metric)
            if dimensions:
                assert _query(con, name, dimensions, [metric]), (view.name, metric)


def _create_views(
    con: Any, project: Path, relations: dict[tuple[str, Any], str], *, tags: bool
) -> None:
    """CREATE the orders and order_items views as sem__orders and sem__order_items."""
    views = {v.name: v for v in build_semantic_views(load_project(project), adapter="duckdb").views}
    for name in ("orders", "order_items"):
        sql = _sql(render_duckdb(views[name]).body, relations, tags=tags)
        con.execute(f"CREATE OR REPLACE SEMANTIC VIEW sem__{name} AS {sql}")


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_the_numbers(set_name: str, connection: Any, tags: bool, make_project: MakeProject) -> None:
    """
    Metrics by country and month, worked out by hand from the fixture's rows.

    gb has orders 1 (10.00, paid) and 3 (7.50, returned) in January and 4 (40.00) in
    February; fr has 2 (25.00) in January and 5 (12.50) in February. Order 6 (5.00, in
    February) has a customer missing from customers, so its country is NULL. Order 1 has
    two items.
    """
    con = connection
    _create_views(con, make_project(set_name), _build_models(con, set_name), tags=tags)
    orders = _query(
        con,
        "sem__orders",
        ["customer__country", "order_month"],
        ["revenue", "paid_revenue", "order_count", "average_order_value", "revenue_doubled"],
    )
    assert sorted(
        (
            (c, m.month, float(r), float(p), int(n), float(a), float(d))
            for c, m, r, p, n, a, d in orders
        ),
        key=str,
    ) == [
        ("fr", 1, 25.0, 25.0, 1, 25.0, 50.0),
        ("fr", 2, 12.5, 12.5, 1, 12.5, 25.0),
        ("gb", 1, 17.5, 10.0, 2, 8.75, 35.0),
        ("gb", 2, 40.0, 40.0, 1, 40.0, 80.0),
        (None, 2, 5.0, 5.0, 1, 5.0, 10.0),
    ]
    items = _query(
        con, "sem__order_items", ["order__customer__country", "ordered_at"], ["items_sold"]
    )
    assert sorted(
        ((country, day.isoformat(), int(sold)) for country, day, sold in items), key=str
    ) == [
        ("fr", "2024-01-10", 4),
        ("fr", "2024-02-15", 1),
        ("gb", "2024-01-03", 3),
        ("gb", "2024-02-02", 3),
        (None, "2024-02-20", 2),
    ]


# Each saved query's export, as a query of the generated semantic view: (view, dimensions,
# metrics), with the columns in the export's order. A saved query's `where` on a dimension
# becomes (dimension, value): the view groups by it too, and the rows that match are summed.
# (where_clause would say it directly, but extension 0.11, in dbt Fusion, has none.)
SAVED_QUERY_AS_VIEW_QUERY: dict[str, tuple[str, list[str], list[str], tuple[str, str] | None]] = {
    "revenue_by_day": ("sem__orders", ["ordered_at"], ["revenue"], None),
    "revenue_by_region_country": (
        "sem__orders",
        ["customer__country", "store__region"],
        ["revenue"],
        ("status", "paid"),
    ),
    "items_by_country": ("sem__order_items", ["order__customer__country"], ["items_sold"], None),
}
# A cumulative metric has no semantic-view form: its oracle is the view's daily revenue
# summed over every day up to each day of the time spine.
CUMULATIVE_FROM_VIEW = """
    SELECT spine.date_day, SUM(daily.revenue)
    FROM {spine} AS spine
    JOIN semantic_view('sem__orders', dimensions := ['ordered_at'], metrics := ['revenue'])
        AS daily ON daily.ordered_at <= spine.date_day
    GROUP BY spine.date_day
"""


def _rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Rows in a comparable form: a midnight timestamp is its date, sorted."""

    def value(v: Any) -> Any:
        if isinstance(v, dt.datetime):
            assert v.time() == dt.time(0), v
            return v.date()
        return v

    return sorted((tuple(value(v) for v in row) for row in rows), key=repr)


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_exports_agree_with_the_semantic_views(
    set_name: str, connection: Any, tags: bool, make_project: MakeProject
) -> None:
    """
    Every saved-query export, run on DuckDB, returns what the semantic views return.

    The exports are SQL MetricFlow writes; the views are SQL the extension writes from the
    view definitions dbt-s-layer generates. Two independent compilers of the same YAML must
    agree on the numbers, row for row.
    """
    con = connection
    relations = _build_models(con, set_name)
    project = make_project(set_name)
    _create_views(con, project, relations, tags=tags)
    exports = render_exports(load_project(project), adapter="duckdb", jobs=1)
    assert {e.saved_query for e in exports} == {*SAVED_QUERY_AS_VIEW_QUERY, "cumulative_revenue"}
    for export in exports:
        actual = _rows(con.execute(_sql(export.body, relations, tags=tags)).fetchall())
        assert len(actual) > 1, export.file_name
        if export.saved_query == "cumulative_revenue":
            spine = relations[("metricflow_time_spine", None)]
            expected = _rows(con.execute(CUMULATIVE_FROM_VIEW.format(spine=spine)).fetchall())
        else:
            view, dimensions, metrics, where = SAVED_QUERY_AS_VIEW_QUERY[export.saved_query]
            if where is None:
                expected = _rows(_query(con, view, dimensions, metrics))
            else:
                # The view's columns: the dimensions, the filtered one, then the metrics. Every
                # metric here sums, so the matching groups add up.
                n = len(dimensions)
                sums: dict[tuple[Any, ...], list[Any]] = {}
                for row in _query(con, view, [*dimensions, where[0]], metrics):
                    if row[n] == where[1]:
                        key, values = tuple(row[:n]), row[n + 1 :]
                        sums[key] = [
                            a + b
                            for a, b in zip(sums.get(key, [0] * len(values)), values, strict=True)
                        ]
                expected = _rows([(*key, *values) for key, values in sums.items()])
        assert actual == expected, export.file_name
