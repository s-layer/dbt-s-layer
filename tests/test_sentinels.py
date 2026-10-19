"""Tests for the sentinel table: physical relations -> tokens -> dbt refs (EXPRT-02, 04, 05)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import jinja2
import pytest

from dbt_s_layer.errors import ExportRenderError, SentinelCollisionError
from dbt_s_layer.project import load_project
from dbt_s_layer.sentinels import (
    RESERVED_PREFIXES,
    build_sentinel_table,
    escape_jinja,
    postflight,
    render_ref,
    rewrite,
)
from tests.conftest import EXPORT_SETS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dbt_s_layer.project import SemanticProject
    from tests.conftest import MakeProject

TOKEN_RE = re.compile(r"^(__REF__|__REFV__|__XREF__)\.\w+$")


def _project(make_project: MakeProject, set_name: str) -> SemanticProject:
    return load_project(make_project(set_name))


def _mutate(
    make_project: MakeProject,
    set_name: str,
    file: str,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    project_dir = make_project(set_name)
    path = project_dir / "target" / file
    raw: dict[str, Any] = json.loads(path.read_text())
    mutate(raw)
    path.write_text(json.dumps(raw))
    return project_dir


def _render(text: str) -> str:
    """Render text the way dbt would, with stub ref() and config()."""

    def ref(*args: object, **kwargs: object) -> str:
        parts = [str(a) for a in args] + [f"v{v}" for v in kwargs.values()]
        return "REL<" + ".".join(parts) + ">"

    def config(**_: object) -> str:
        return ""

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return env.from_string(text).render(ref=ref, config=config)


def _relations(project: SemanticProject) -> dict[str, str]:
    return {
        sm.name: sm.node_relation.relation_name for sm in project.semantic_manifest.semantic_models
    }


# --- tokens -----------------------------------------------------------------


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_every_relation_becomes_a_two_part_token(set_name: str, make_project: MakeProject) -> None:
    project = _project(make_project, set_name)
    table = build_sentinel_table(project)
    tokens = _relations_of(table.manifest)
    assert set(tokens) == {"customers", "order_items", "orders", "stores"}
    for token in tokens.values():
        assert TOKEN_RE.match(token), token
        assert token in table.relations
    assert len(set(tokens.values())) == len(tokens)
    spine = table.manifest.project_configuration.time_spines[0].node_relation.relation_name
    assert TOKEN_RE.match(spine)
    assert table.relations[spine].jinja == "{{ ref('metricflow_time_spine') }}"


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_the_callers_manifest_is_untouched(set_name: str, make_project: MakeProject) -> None:
    project = _project(make_project, set_name)
    before = _relations(project)
    spine = project.semantic_manifest.project_configuration.time_spines[0]
    spine_before = spine.node_relation.relation_name
    build_sentinel_table(project)
    assert _relations(project) == before
    assert spine.node_relation.relation_name == spine_before
    assert not any(p in project.semantic_manifest.json() for p in RESERVED_PREFIXES)


@pytest.mark.parametrize(
    ("set_name", "expected"),
    [
        (
            "core_export",
            {
                "orders": ("__REF__", "{{ ref('orders') }}"),
                "order_items": ("__REF__", "{{ ref('order_items') }}"),
                "stores": ("__XREF__", "{{ ref('fx_dep', 'stores') }}"),
                "customers": ("__REFV__", "{{ ref('customers', v=1) }}"),
            },
        ),
        (
            "fusion_export",
            {
                "orders": ("__REF__", "{{ ref('orders') }}"),
                "order_items": ("__REF__", "{{ ref('order_items') }}"),
                "stores": ("__XREF__", "{{ ref('fx_dep', 'stores') }}"),
                # Fusion's own expression is unversioned; the version is pinned.
                "customers": ("__REFV__", "{{ ref('customers', v=2) }}"),
            },
        ),
    ],
)
def test_tokens_resolve_to_refs_built_from_the_node(
    set_name: str, expected: dict[str, tuple[str, str]], make_project: MakeProject
) -> None:
    table = build_sentinel_table(_project(make_project, set_name))
    tokens = _relations_of(table.manifest)
    for name, (prefix, jinja) in expected.items():
        assert tokens[name].split(".")[0] == prefix
        assert table.relations[tokens[name]].jinja == jinja


def test_semantic_models_on_one_node_share_a_token(make_project: MakeProject) -> None:
    def second_orders(raw: dict[str, Any]) -> None:
        orders = next(sm for sm in raw["semantic_models"] if sm["name"] == "orders")
        clone = json.loads(json.dumps(orders))
        clone["name"] = "orders_again"
        for entity in clone["entities"]:
            entity["name"] = f"{entity['name']}_again"
        clone["measures"] = [{**m, "name": f"{m['name']}_again"} for m in clone["measures"]]
        clone["dimensions"] = [{**d, "name": f"{d['name']}_again"} for d in clone["dimensions"]]
        clone["defaults"] = {"agg_time_dimension": "ordered_at_again"}
        raw["semantic_models"].append(clone)

    def second_orders_dbt(raw: dict[str, Any]) -> None:
        orders = raw["semantic_models"]["semantic_model.fx.orders"]
        raw["semantic_models"]["semantic_model.fx.orders_again"] = {
            **orders,
            "unique_id": "semantic_model.fx.orders_again",
            "name": "orders_again",
        }

    project_dir = _mutate(make_project, "core_export", "semantic_manifest.json", second_orders)
    path = project_dir / "target" / "manifest.json"
    raw = json.loads(path.read_text())
    second_orders_dbt(raw)
    path.write_text(json.dumps(raw))
    table = build_sentinel_table(load_project(project_dir))
    tokens = _relations_of(table.manifest)
    assert tokens["orders"] == tokens["orders_again"]


# --- time spines ------------------------------------------------------------


def test_legacy_time_spine_configuration_is_mapped(make_project: MakeProject) -> None:
    def to_legacy(raw: dict[str, Any]) -> None:
        config = raw["project_configuration"]
        spine = config["time_spines"].pop()
        config["time_spine_table_configurations"] = [
            {
                "location": spine["node_relation"]["relation_name"],
                "column_name": spine["primary_column"]["name"],
                "grain": "day",
            }
        ]

    project_dir = _mutate(make_project, "core_export", "semantic_manifest.json", to_legacy)
    table = build_sentinel_table(load_project(project_dir))
    config = table.manifest.project_configuration
    assert config.time_spines == []
    location = config.time_spine_table_configurations[0].location
    assert TOKEN_RE.match(location)
    assert table.relations[location].jinja == "{{ ref('metricflow_time_spine') }}"


def test_unmapped_time_spine_fails_only_where_used(make_project: MakeProject) -> None:
    def elsewhere(raw: dict[str, Any]) -> None:
        relation = raw["project_configuration"]["time_spines"][0]["node_relation"]
        relation["relation_name"] = '"memory"."marts"."not_a_node"'

    project_dir = _mutate(make_project, "core_export", "semantic_manifest.json", elsewhere)
    table = build_sentinel_table(load_project(project_dir))
    spine = table.manifest.project_configuration.time_spines[0].node_relation.relation_name
    assert table.relations[spine].jinja is None
    orders = _relations_of(table.manifest)["orders"]
    assert "ref('orders')" in rewrite(f"select * from {orders} o", table, context="exp_a")
    with pytest.raises(ExportRenderError, match=r"exp_b.*not_a_node.*no node in manifest\.json"):
        rewrite(f"select * from {spine} s", table, context="exp_b")


# --- ref rendering ----------------------------------------------------------


@pytest.mark.parametrize(
    ("package", "name", "version", "expected"),
    [
        ("fx", "orders", None, "{{ ref('orders') }}"),
        ("dep", "orders", None, "{{ ref('dep', 'orders') }}"),
        ("fx", "customers", 3, "{{ ref('customers', v=3) }}"),
        ("dep", "customers", 3, "{{ ref('dep', 'customers', v=3) }}"),
        ("fx", "customers", 1.5, "{{ ref('customers', v=1.5) }}"),
        ("fx", "customers", "beta", "{{ ref('customers', v='beta') }}"),
        ("fx", "it's", None, "{{ ref('it\\'s') }}"),
        ("fx", "back\\slash", None, "{{ ref('back\\\\slash') }}"),
    ],
)
def test_render_ref(package: str, name: str, version: float | str | None, expected: str) -> None:
    assert render_ref(package, name, version, root_package="fx") == expected


def test_rendered_refs_round_trip_through_jinja() -> None:
    for name in ("it's", "back\\slash", "plain"):
        assert _render(render_ref("dep", name, "a'b", root_package="fx")) == (
            f"REL<dep.{name}.va'b>"
        )


# --- escaping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "select 1",
        "select '{{ x }}' as a",
        "select '{%' as a, '{#' as b, '#}' as c, '%}' as d, '}}' as e",
        "{{{",
        "{%}",
        "a{",
        "{",
        "}{{}}{",
        "{{ '{{' }}",
    ],
)
def test_escaped_text_renders_back_to_itself(text: str) -> None:
    assert _render(escape_jinja(text)) == text


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_rewrite_escapes_text_but_not_refs(set_name: str, make_project: MakeProject) -> None:
    table = build_sentinel_table(_project(make_project, set_name))
    orders = _relations_of(table.manifest)["orders"]
    # A literal `{` right before the token must not merge with the ref's `{{`.
    sql = f"select '{{%' as a from{{{orders} o where x = '{{{{'"
    out = rewrite(sql, table, context="exp")
    assert "{{ ref('orders') }}" in out
    assert _render(out) == sql.replace(orders, "REL<orders>")
    postflight(out, context="exp")


# --- pre-flight (EXPRT-04) --------------------------------------------------


@pytest.mark.parametrize("prefix", RESERVED_PREFIXES)
def test_preflight_rejects_a_prefix_in_semantic_sql(prefix: str, make_project: MakeProject) -> None:
    def poison(raw: dict[str, Any]) -> None:
        orders = next(sm for sm in raw["semantic_models"] if sm["name"] == "orders")
        dim = next(d for d in orders["dimensions"] if d["name"] == "status")
        dim["expr"] = f"case when status = '{prefix.lower()}.n0_orders' then 1 end"

    project_dir = _mutate(make_project, "core_export", "semantic_manifest.json", poison)
    with pytest.raises(SentinelCollisionError) as info:
        build_sentinel_table(load_project(project_dir))
    message = str(info.value)
    assert prefix in message
    assert "semantic_models[orders].dimensions[status].expr" in message


def test_preflight_rejects_a_prefix_in_a_saved_query_filter(make_project: MakeProject) -> None:
    def poison(raw: dict[str, Any]) -> None:
        sq = next(q for q in raw["saved_queries"] if q["name"] == "revenue_by_region_country")
        sq["query_params"]["where"]["where_filters"][0]["where_sql_template"] += (
            " or 1 = '__XREF__'"
        )

    project_dir = _mutate(make_project, "fusion_export", "semantic_manifest.json", poison)
    with pytest.raises(SentinelCollisionError, match=r"saved_queries\[revenue_by_region_country\]"):
        build_sentinel_table(load_project(project_dir))


def test_preflight_rejects_a_prefix_in_a_referenced_model(make_project: MakeProject) -> None:
    def poison(raw: dict[str, Any]) -> None:
        raw["nodes"]["model.fx_dep.stores"]["alias"] = "__Source__stores"

    project_dir = _mutate(make_project, "core_export", "manifest.json", poison)
    with pytest.raises(SentinelCollisionError, match=r"model\.fx_dep\.stores.*__SOURCE__"):
        build_sentinel_table(load_project(project_dir))


def test_preflight_ignores_models_no_semantic_model_reads(make_project: MakeProject) -> None:
    def unrelated(raw: dict[str, Any]) -> None:
        node = dict(raw["nodes"]["model.fx.orders"])
        node.update(unique_id="model.fx.__ref__scratch", name="__ref__scratch")
        node["alias"] = "__ref__scratch"
        raw["nodes"]["model.fx.__ref__scratch"] = node

    project_dir = _mutate(make_project, "core_export", "manifest.json", unrelated)
    build_sentinel_table(load_project(project_dir))


# --- post-flight (EXPRT-05) -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "select * from __REF__.n9_gone",
        "select * from __refv__.n0_customers",
        "select '__XREF__' as a",
        "select 1 from x where y = '__source__.raw'",
    ],
)
def test_postflight_refuses_leftover_prefixes(text: str) -> None:
    with pytest.raises(ExportRenderError, match=r"exp_a.*unresolved sentinel"):
        postflight(text, context="exp_a")


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_rewrite_refuses_unknown_tokens(set_name: str, make_project: MakeProject) -> None:
    table = build_sentinel_table(_project(make_project, set_name))
    with pytest.raises(ExportRenderError, match=r"exp_a.*__REF__\.n99_nope"):
        rewrite("select * from __REF__.n99_nope", table, context="exp_a")


def _relations_of(manifest: Any) -> dict[str, str]:
    return {sm.name: sm.node_relation.relation_name for sm in manifest.semantic_models}


def test_preflight_checks_time_spine_nodes(make_project: MakeProject) -> None:
    def poison(raw: dict[str, Any]) -> None:
        raw["nodes"]["model.fx.metricflow_time_spine"]["name"] = "__ref__spine"

    project_dir = _mutate(make_project, "core_export", "manifest.json", poison)
    with pytest.raises(SentinelCollisionError, match=r"metricflow_time_spine.*__REF__"):
        build_sentinel_table(load_project(project_dir))
