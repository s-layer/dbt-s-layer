"""
Guards on the committed parse-artifact corpus under tests/fixtures/artifacts/.

These catch a bad `scripts/regen_fixtures.py` run before it reaches the loader
tests: a missing producer, an untrimmed manifest, or a lost semantic model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ARTIFACTS = Path(__file__).parent / "fixtures" / "artifacts"

# artifact set -> expected PRODUCER prefix
SETS = {
    "fusion_new": "Fusion 2.",
    "core_new": "dbt Core 1.",
    "core_legacy": "dbt Core 1.",
}


def _load(set_name: str, file: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ARTIFACTS / set_name / file).read_text())
    return data


def _names(items: list[dict[str, Any]]) -> set[str]:
    return {item["name"] for item in items}


def test_corpus_has_exactly_the_expected_sets() -> None:
    assert sorted(p.name for p in ARTIFACTS.iterdir() if p.is_dir()) == sorted(SETS)


@pytest.mark.parametrize(("set_name", "producer"), SETS.items())
def test_producer_names_the_engine(set_name: str, producer: str) -> None:
    text = (ARTIFACTS / set_name / "PRODUCER").read_text()
    assert text.startswith(producer)
    assert text.endswith("\n")
    assert text.count("\n") == 1


@pytest.mark.parametrize("set_name", SETS)
def test_producer_matches_manifest_dbt_version(set_name: str) -> None:
    version = _load(set_name, "manifest.json")["metadata"]["dbt_version"]
    assert (ARTIFACTS / set_name / "PRODUCER").read_text().strip().endswith(f" {version}")


@pytest.mark.parametrize("set_name", SETS)
def test_semantic_manifest_has_orders_and_saved_query(set_name: str) -> None:
    semantic = _load(set_name, "semantic_manifest.json")
    assert {"orders", "order_items"} <= _names(semantic["semantic_models"])
    assert {"revenue", "items_sold"} <= _names(semantic["metrics"])
    saved = {sq["name"]: sq for sq in semantic["saved_queries"]}
    assert set(saved) == {"revenue_by_day"}
    assert [e["name"] for e in saved["revenue_by_day"]["exports"]] == ["exp_revenue_by_day"]


@pytest.mark.parametrize("set_name", SETS)
def test_manifest_is_trimmed(set_name: str) -> None:
    manifest = _load(set_name, "manifest.json")
    assert set(manifest) == {"metadata", "nodes", "semantic_models"}
    assert set(manifest["metadata"]) <= {
        "adapter_type",
        "dbt_schema_version",
        "dbt_version",
        "project_name",
        "quoting",
    }
    assert {n["resource_type"] for n in manifest["nodes"].values()} == {"model"}
    for node in manifest["nodes"].values():
        assert "created_at" not in node


@pytest.mark.parametrize("set_name", SETS)
def test_semantic_model_dependencies_exist_in_nodes(set_name: str) -> None:
    manifest = _load(set_name, "manifest.json")
    assert manifest["semantic_models"]
    for sm in manifest["semantic_models"].values():
        assert sm["depends_on"]["nodes"]
        for node_id in sm["depends_on"]["nodes"]:
            assert node_id in manifest["nodes"]


@pytest.mark.parametrize("set_name", SETS)
def test_both_artifacts_list_the_same_semantic_models(set_name: str) -> None:
    semantic = _names(_load(set_name, "semantic_manifest.json")["semantic_models"])
    in_manifest = {sm["name"] for sm in _load(set_name, "manifest.json")["semantic_models"].values()}
    assert semantic == in_manifest


@pytest.mark.parametrize("set_name", SETS)
def test_artifacts_hold_no_machine_paths(set_name: str) -> None:
    root = str(Path(__file__).resolve().parent.parent)
    for file in ("semantic_manifest.json", "manifest.json"):
        assert root not in (ARTIFACTS / set_name / file).read_text()


def test_core_drops_semantic_models_on_versioned_models() -> None:
    # dbt Core 1.12.6 silently drops a new-spec semantic model attached to a
    # versioned model; Fusion keeps it. See
    # .planning/phases/01-parsing-pipeline/01-RESEARCH.md ("Versioned models").
    # If a Core regen makes this fail, Core has fixed the drop: update the
    # research note, the missing-model warning, and this test.
    assert "customers" in _names(_load("fusion_new", "semantic_manifest.json")["semantic_models"])
    assert "customers" not in _names(_load("core_new", "semantic_manifest.json")["semantic_models"])
