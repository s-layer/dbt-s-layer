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
BASE_SETS = {
    "fusion_new": "Fusion 2.",
    "core_new": "dbt Core 1.",
    "core_legacy": "dbt Core 1.",
}
# Sets parsed from the export fixture projects (Phase 2).
EXPORT_SETS = {
    "fusion_export": "Fusion 2.",
    "core_export": "dbt Core 1.",
}
# Sets parsed on a Snowflake target with dbt_semantic_view installed (Phase 4).
SNOWFLAKE_SETS = {
    "fusion_snowflake": "Fusion 2.",
    "core_snowflake": "dbt Core 1.",
}
SETS = {**BASE_SETS, **EXPORT_SETS, **SNOWFLAKE_SETS}
SEMANTIC_VIEW_MACRO = "macro.dbt_semantic_view.materialization_semantic_view_snowflake"
EXPORT_SAVED_QUERIES = {
    "cumulative_revenue",
    "items_by_country",
    "revenue_by_day",
    "revenue_by_region_country",
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


@pytest.mark.parametrize("set_name", BASE_SETS)
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
    assert set(manifest) - {"macros"} == {"metadata", "nodes", "saved_queries", "semantic_models"}
    # Only the dbt_semantic_view materializations, by id, and only where it is installed.
    assert ("macros" in manifest) == (set_name in SNOWFLAKE_SETS)
    for macro_id, macro in manifest.get("macros", {}).items():
        assert macro_id.startswith("macro.dbt_semantic_view.materialization_")
        assert macro == {"unique_id": macro_id, "package_name": "dbt_semantic_view"}
    assert set(manifest["metadata"]) <= {
        "adapter_type",
        "dbt_schema_version",
        "dbt_version",
        "project_name",
        "quoting",
    }
    # The fixture projects have no seeds or snapshots, so only models remain.
    assert {n["resource_type"] for n in manifest["nodes"].values()} == {"model"}
    for entry in [*manifest["nodes"].values(), *manifest["saved_queries"].values()]:
        assert "created_at" not in entry
    semantic = _load(set_name, "semantic_manifest.json")
    assert {sq_id.rsplit(".", 1)[1] for sq_id in manifest["saved_queries"]} == _names(
        semantic["saved_queries"]
    )


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
    in_manifest = {
        sm["name"] for sm in _load(set_name, "manifest.json")["semantic_models"].values()
    }
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


def _export_configs(set_name: str) -> dict[str, dict[str, Any]]:
    semantic = _load(set_name, "semantic_manifest.json")
    return {
        export["name"]: export["config"]
        for sq in semantic["saved_queries"]
        for export in sq["exports"]
    }


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_export_sets_cover_every_ref_form(set_name: str) -> None:
    semantic = _load(set_name, "semantic_manifest.json")
    manifest = _load(set_name, "manifest.json")
    assert _names(semantic["semantic_models"]) == {"customers", "order_items", "orders", "stores"}
    assert _names(semantic["saved_queries"]) == EXPORT_SAVED_QUERIES
    # A package-owned semantic model (cross-package ref).
    stores = manifest["semantic_models"]["semantic_model.fx_dep.stores"]
    assert stores["depends_on"]["nodes"] == ["model.fx_dep.stores"]
    # A semantic model on a versioned node: dbt Core pins v1 (legacy spec),
    # Fusion follows latest_version (v2).
    version = 1 if set_name == "core_export" else 2
    customers = manifest["semantic_models"]["semantic_model.fx.customers"]
    assert customers["depends_on"]["nodes"] == [f"model.fx.customers.v{version}"]
    # The time spine is a node, so it can be turned into a ref.
    assert "model.fx.metricflow_time_spine" in manifest["nodes"]


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_export_sets_record_what_the_user_configured(set_name: str) -> None:
    manifest = _load(set_name, "manifest.json")
    by_day = manifest["saved_queries"]["saved_query.fx.revenue_by_day"]
    unrendered = {e["name"]: e["unrendered_config"] for e in by_day["exports"]}
    assert unrendered["exp_revenue_by_day"] == {"export_as": "table"}
    assert unrendered["exp_revenue_by_day_view"] == {
        "export_as": "view",
        "alias": "revenue_daily",
        "schema": "reporting",
    }
    items = manifest["saved_queries"]["saved_query.fx.items_by_country"]
    assert items["config"]["export_as"] == "view"
    assert items["config"]["schema"] == "sq_schema"
    assert [e["unrendered_config"] for e in items["exports"]] == [{}]


def test_fusion_does_not_inherit_saved_query_export_as() -> None:
    # A saved query's `config: {export_as: view}` should apply to exports that
    # set none. dbt Core 1.12.6 applies it; Fusion 2.0.8 writes `table`. See
    # .planning/phases/02-export-sql-generation/02-RESEARCH.md (finding 4).
    # dbt-s-layer resolves export_as from manifest.json, so both give a view.
    # If a Fusion regen makes this fail, Fusion has fixed it: update the note.
    assert _export_configs("core_export")["exp_items_by_country"]["export_as"] == "view"
    assert _export_configs("fusion_export")["exp_items_by_country"]["export_as"] == "table"


@pytest.mark.parametrize("set_name", SNOWFLAKE_SETS)
def test_snowflake_sets_have_the_semantic_view_package(set_name: str) -> None:
    manifest = _load(set_name, "manifest.json")
    assert manifest["metadata"]["adapter_type"] == "snowflake"
    assert SEMANTIC_VIEW_MACRO in manifest["macros"]


@pytest.mark.parametrize("set_name", SNOWFLAKE_SETS)
def test_snowflake_sets_cover_the_metric_mapping(set_name: str) -> None:
    semantic = _load(set_name, "semantic_manifest.json")
    assert _names(semantic["semantic_models"]) == {"customers", "order_items", "orders", "stores"}
    kinds = {m["name"]: m["type"] for m in semantic["metrics"]}
    assert kinds["average_order_value"] == "ratio"
    assert kinds["revenue_doubled"] == "derived"
    assert kinds["revenue_per_item"] == "derived"
    assert kinds["revenue_cumulative"] == "cumulative"
    assert kinds["revenue_p90"] == "simple"
    assert kinds["latest_revenue"] == "simple"
