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
unless DBT_S_LAYER_DUCKDB_EXTENSION=required, as in CI.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

import jinja2
import pytest

from dbt_s_layer.duckdb_views import render_duckdb
from dbt_s_layer.project import load_project
from dbt_s_layer.semantic_views import build_semantic_views
from tests.conftest import ARTIFACTS, DUCKDB_SETS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.conftest import MakeProject

duckdb = pytest.importorskip("duckdb")

# `  [PRIVATE ]<alias>.<name> ...` or `  [PRIVATE ]<name> AS ...` (a derived metric).
_MEMBER_RE = re.compile(r"^  (PRIVATE )?(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\b")


@pytest.fixture(scope="module")
def connection() -> Iterator[Any]:
    expected = os.environ.get("DBT_S_LAYER_DUCKDB_VERSION")
    if expected is not None:
        assert duckdb.__version__ == expected
    con = duckdb.connect()
    try:
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")
    except duckdb.Error as exc:
        if os.environ.get("DBT_S_LAYER_DUCKDB_EXTENSION") == "required":
            raise
        pytest.skip(f"cannot load the semantic_views extension: {exc}")
    yield con
    con.close()


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


def _sql(body: str, relations: dict[tuple[str, Any], str]) -> str:
    def ref(*args: str, v: Any = None, version: Any = None) -> str:
        return relations[(args[-1], v if v is not None else version)]

    def config(**_: object) -> str:
        return ""

    return jinja2.Environment(autoescape=False).from_string(body).render(ref=ref, config=config)


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
    set_name: str, connection: Any, make_project: MakeProject
) -> None:
    con = connection
    relations = _build_models(con, set_name)
    project = load_project(make_project(set_name))
    views = build_semantic_views(project, adapter="duckdb").views
    assert {v.name for v in views} == {"customers", "order_items", "orders", "stores"}
    for view in views:
        body = render_duckdb(view).body
        name = f"sem__{set_name}_{view.name}"
        con.execute(f"CREATE OR REPLACE SEMANTIC VIEW {name} AS {_sql(body, relations)}")
        dimensions, metrics = _members(body)
        assert dimensions or metrics, view.name
        if dimensions:
            assert len(_query(con, name, dimensions, [])) == 1, view.name
        for metric in metrics:
            assert _query(con, name, [], [metric]), (view.name, metric)
            if dimensions:
                assert _query(con, name, dimensions, [metric]), (view.name, metric)


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_the_numbers(set_name: str, connection: Any, make_project: MakeProject) -> None:
    con = connection
    relations = _build_models(con, set_name)
    views = {
        v.name: v
        for v in build_semantic_views(load_project(make_project(set_name)), adapter="duckdb").views
    }
    for name in ("orders", "order_items"):
        body = render_duckdb(views[name]).body
        con.execute(f"CREATE OR REPLACE SEMANTIC VIEW sem__{name} AS {_sql(body, relations)}")
    orders = _query(
        con,
        "sem__orders",
        ["customer__country", "order_month"],
        ["revenue", "paid_revenue", "average_order_value", "revenue_doubled"],
    )
    assert [(c, float(r), float(p), float(a), float(d)) for c, _, r, p, a, d in orders] == [
        ("gb", 10.0, 10.0, 10.0, 20.0)
    ]
    items = _query(
        con, "sem__order_items", ["order__customer__country", "ordered_at"], ["items_sold"]
    )
    assert [(country, int(sold)) for country, _, sold in items] == [("gb", 2)]
