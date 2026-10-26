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
# `NAME = 'value'` in a TAG clause; `''` is a quote.
_TAG_RE = re.compile(r"(\w+) = '((?:[^']|'')*)'")
# Members with an `s_layer` block a view leaves out: a joined table's key entity.
SKIPPED_TAGGED = {"order_items": 1}
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
            assert len(_query(con, name, dimensions, [])) == 1, view.name
        for metric in metrics:
            assert _query(con, name, [], [metric]), (view.name, metric)
            if dimensions:
                assert _query(con, name, dimensions, [metric]), (view.name, metric)


@pytest.mark.parametrize("set_name", DUCKDB_SETS)
def test_the_numbers(set_name: str, connection: Any, tags: bool, make_project: MakeProject) -> None:
    con = connection
    relations = _build_models(con, set_name)
    views = {
        v.name: v
        for v in build_semantic_views(load_project(make_project(set_name)), adapter="duckdb").views
    }
    for name in ("orders", "order_items"):
        body = render_duckdb(views[name]).body
        sql = _sql(body, relations, tags=tags)
        con.execute(f"CREATE OR REPLACE SEMANTIC VIEW sem__{name} AS {sql}")
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
