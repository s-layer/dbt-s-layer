"""Tests for the semantic model -> dbt node map (PARSE-03)."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest

from dbt_s_layer.errors import RefResolutionError
from dbt_s_layer.project import load_project
from dbt_s_layer.refs import RefTarget, build_ref_map
from tests.conftest import ARTIFACT_SETS, ARTIFACTS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import MakeProject

EXPECTED_KEYS = {
    "fusion_new": ["customers", "order_items", "orders"],
    "core_new": ["order_items", "orders"],
    "core_legacy": ["order_items", "orders"],
}
# (database, schema) each producer resolved the fixture profile to.
RELATION_PREFIX = {
    "fusion_new": ("ANALYTICS", "MARTS"),
    "core_new": ("memory", "marts"),
    "core_legacy": ("memory", "marts"),
}


def _physical_relations(set_name: str) -> dict[str, str]:
    raw = json.loads((ARTIFACTS / set_name / "semantic_manifest.json").read_text())
    return {sm["name"]: sm["node_relation"]["relation_name"] for sm in raw["semantic_models"]}


def _mutated_map(
    make_project: MakeProject, set_name: str, mutate: Callable[[dict[str, Any]], None]
) -> Path:
    project_dir = make_project(set_name)
    path = project_dir / "target" / "manifest.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    mutate(raw)
    path.write_text(json.dumps(raw))
    return project_dir


# --- positive cases ---------------------------------------------------------


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_every_semantic_model_maps_to_one_target(set_name: str, make_project: MakeProject) -> None:
    project = load_project(make_project(set_name))
    refs = build_ref_map(project)
    assert list(refs) == EXPECTED_KEYS[set_name]
    assert {sm.name for sm in project.semantic_manifest.semantic_models} == set(refs)
    for key, target in refs.items():
        assert target.semantic_model == key


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_orders_target(set_name: str, make_project: MakeProject) -> None:
    refs = build_ref_map(load_project(make_project(set_name)))
    database, schema = RELATION_PREFIX[set_name]
    assert refs["orders"] == RefTarget(
        semantic_model="orders",
        node_id="model.fx.orders",
        package="fx",
        model_name="orders",
        version=None,
        ref_expression="ref('orders')",
        database=database,
        schema_name=schema,
        alias="orders",
    )


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_mixed_case_alias_keeps_its_case(set_name: str, make_project: MakeProject) -> None:
    target = build_ref_map(load_project(make_project(set_name)))["order_items"]
    assert target.alias == "Order_Items"
    assert target.ref_expression == "ref('order_items')"


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_parts_reproduce_the_physical_relation(set_name: str, make_project: MakeProject) -> None:
    # Fusion leaves relation_name unquoted, dbt Core quotes it; the parts are the
    # same either way, so nothing downstream needs to parse relation_name.
    refs = build_ref_map(load_project(make_project(set_name)))
    quote = '"' if set_name.startswith("core") else ""
    for name, relation_name in _physical_relations(set_name).items():
        target = refs[name]
        parts = (target.database, target.schema_name, target.alias)
        assert relation_name == ".".join(f"{quote}{p}{quote}" for p in parts)


def test_versioned_model_on_fusion(make_project: MakeProject) -> None:
    target = build_ref_map(load_project(make_project("fusion_new")))["customers"]
    assert target.node_id == "model.fx.customers.v2"
    assert target.model_name == "customers"
    assert target.version == 2
    # The user's unpinned ref stays unpinned.
    assert target.ref_expression == "ref('customers')"
    assert target.alias == "customers_v2"


def test_map_is_read_only(make_project: MakeProject) -> None:
    refs = build_ref_map(load_project(make_project("core_new")))
    assert isinstance(refs, MappingProxyType)


def test_node_from_another_package_sets_package(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["nodes"]["model.fx.orders"]["package_name"] = "upstream"

    project_dir = _mutated_map(make_project, "core_new", mutate)
    target = build_ref_map(load_project(project_dir))["orders"]
    assert target.package == "upstream"


def test_semantic_model_name_matches_case_insensitively(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["semantic_models"]["semantic_model.fx.orders"]["name"] = "Orders"

    project_dir = _mutated_map(make_project, "core_new", mutate)
    assert build_ref_map(load_project(project_dir))["orders"].node_id == "model.fx.orders"


# --- failure cases: each gets its own test ----------------------------------


def _assert_resolution_error(project_dir: Path, *fragments: str) -> None:
    with pytest.raises(RefResolutionError) as exc:
        build_ref_map(load_project(project_dir))
    message = str(exc.value)
    assert "'orders'" in message
    assert "dbt Core 1.12.6" in message
    for fragment in fragments:
        assert fragment in message


def test_no_upstream_model_node(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["semantic_models"]["semantic_model.fx.orders"]["depends_on"]["nodes"] = []

    _assert_resolution_error(_mutated_map(make_project, "core_new", mutate), "no model")


def test_only_non_model_upstream_nodes(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        deps = raw["semantic_models"]["semantic_model.fx.orders"]["depends_on"]
        deps["nodes"] = ["source.fx.raw.orders"]

    _assert_resolution_error(_mutated_map(make_project, "core_new", mutate), "no model")


def test_two_upstream_model_nodes(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        deps = raw["semantic_models"]["semantic_model.fx.orders"]["depends_on"]
        deps["nodes"] = ["model.fx.orders", "model.fx.order_items"]

    _assert_resolution_error(
        _mutated_map(make_project, "core_new", mutate), "model.fx.orders", "model.fx.order_items"
    )


def test_upstream_node_missing_from_nodes(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        deps = raw["semantic_models"]["semantic_model.fx.orders"]["depends_on"]
        deps["nodes"] = ["model.fx.gone"]

    _assert_resolution_error(_mutated_map(make_project, "core_new", mutate), "model.fx.gone")


def test_semantic_model_missing_from_dbt_manifest(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        del raw["semantic_models"]["semantic_model.fx.orders"]

    _assert_resolution_error(_mutated_map(make_project, "core_new", mutate), "manifest.json")


def test_semantic_model_without_ref_expression(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["semantic_models"]["semantic_model.fx.orders"]["model"] = None

    _assert_resolution_error(_mutated_map(make_project, "core_new", mutate), "ref()")


def test_semantic_model_name_in_two_packages(make_project: MakeProject) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        sms = raw["semantic_models"]
        copy = json.loads(json.dumps(sms["semantic_model.fx.orders"]))
        copy["package_name"] = "upstream"
        copy["unique_id"] = "semantic_model.upstream.orders"
        sms["semantic_model.upstream.orders"] = copy

    _assert_resolution_error(
        _mutated_map(make_project, "core_new", mutate),
        "semantic_model.fx.orders",
        "semantic_model.upstream.orders",
    )
