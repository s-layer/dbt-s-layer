"""Tests for load_project / SemanticProject (PARSE-01, PARSE-02)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_s_layer.errors import ArtifactInvalidError, ArtifactNotFoundError
from dbt_s_layer.project import load_project, producer_label
from tests.conftest import ARTIFACT_SETS, MakeProject

# What each fixture set must load as. dbt Core drops `customers` (a semantic
# model on a versioned model), so its sets lack it and its metric.
EXPECTED = {
    "fusion_new": {
        "producer": "Fusion 2.0.8",
        "semantic_models": {"customers", "order_items", "orders"},
        "metrics": {"cust_total", "items_sold", "revenue"},
    },
    "core_new": {
        "producer": "dbt Core 1.12.6",
        "semantic_models": {"order_items", "orders"},
        "metrics": {"items_sold", "revenue"},
    },
    "core_legacy": {
        "producer": "dbt Core 1.12.6",
        "semantic_models": {"order_items", "orders"},
        "metrics": {"items_sold", "revenue"},
    },
}


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_load_project_contents(set_name: str, make_project: MakeProject) -> None:
    project_dir = make_project(set_name)
    project = load_project(project_dir)
    expected = EXPECTED[set_name]
    manifest = project.semantic_manifest
    assert {sm.name for sm in manifest.semantic_models} == expected["semantic_models"]
    assert {m.name for m in manifest.metrics} == expected["metrics"]
    assert {sq.name for sq in manifest.saved_queries} == {"revenue_by_day"}
    assert [e.name for e in manifest.saved_queries[0].exports] == ["exp_revenue_by_day"]
    assert project.producer == expected["producer"]
    assert project.project_dir == project_dir
    assert project.dbt_manifest.metadata.project_name == "fx"


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_loaded_manifest_builds_a_lookup(set_name: str, make_project: MakeProject) -> None:
    project = load_project(make_project(set_name))
    # A legacy manifest loaded without metricflow's transformations fails here.
    SemanticManifestLookup(project.semantic_manifest)


def test_legacy_and_new_spec_core_sets_agree(make_project: MakeProject) -> None:
    new = load_project(make_project("core_new")).semantic_manifest
    legacy = load_project(make_project("core_legacy")).semantic_manifest
    assert {m.name for m in new.metrics} == {m.name for m in legacy.metrics}
    assert [(e.name, e.config) for e in new.saved_queries[0].exports] == [
        (e.name, e.config) for e in legacy.saved_queries[0].exports
    ]


# dbt Core merges a column's `config.meta` into its new-spec dimension's, the
# dimension's own keys winning. Fusion writes only the dimension's own.
ORDERED_AT_META = {"source": "dimension", "column_only": True, "dimension_only": True}


def _dimension_meta(project_dir: Path, model: str, dimension: str) -> dict[str, object] | None:
    manifest = load_project(project_dir).semantic_manifest
    semantic_model = next(sm for sm in manifest.semantic_models if sm.name == model)
    found = next(d for d in semantic_model.dimensions if d.name == dimension)
    return None if found.config is None else dict(found.config.meta)


@pytest.mark.parametrize("set_name", ["fusion_new", "core_new"])
def test_new_spec_dimension_meta_includes_column_meta(
    set_name: str, make_project: MakeProject
) -> None:
    assert _dimension_meta(make_project(set_name), "orders", "ordered_at") == ORDERED_AT_META


def test_dimension_meta_is_the_same_from_either_engine(make_project: MakeProject) -> None:
    def all_meta(set_name: str) -> dict[tuple[str, str], object]:
        manifest = load_project(make_project(set_name)).semantic_manifest
        return {
            (sm.name, d.name): None if d.config is None else dict(d.config.meta)
            for sm in manifest.semantic_models
            # dbt Core drops `customers` (a versioned model).
            if sm.name != "customers"
            for d in sm.dimensions
        }

    assert all_meta("fusion_new") == all_meta("core_new")


def test_project_is_frozen(make_project: MakeProject) -> None:
    project = load_project(make_project("core_new"))
    with pytest.raises(FrozenInstanceError):
        project.producer = "other"  # pyright: ignore[reportAttributeAccessIssue]


def test_load_project_reads_the_configured_target_path(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    (project_dir / "target").rename(project_dir / "out")
    with (project_dir / "dbt_project.yml").open("a") as f:
        f.write("target-path: out\n")
    assert load_project(project_dir).producer == "dbt Core 1.12.6"


def test_load_project_missing_target(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    (project_dir / "target" / "semantic_manifest.json").unlink()
    with pytest.raises(ArtifactNotFoundError) as exc:
        load_project(project_dir)
    assert "semantic_manifest.json" in str(exc.value)
    assert "`dbt parse`" in str(exc.value)


def test_load_project_missing_dbt_manifest(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    (project_dir / "target" / "manifest.json").unlink()
    with pytest.raises(ArtifactNotFoundError) as exc:
        load_project(project_dir)
    assert "manifest.json" in str(exc.value)


def test_load_project_mismatched_project_names(make_project: MakeProject) -> None:
    # A manifest.json from another project (e.g. a stale copy) would map
    # semantic models to the wrong nodes, so it is rejected.
    project_dir = make_project("core_new")
    path = project_dir / "target" / "manifest.json"
    raw = json.loads(path.read_text())
    raw["metadata"]["project_name"] = "other"
    path.write_text(json.dumps(raw))
    with pytest.raises(ArtifactInvalidError) as exc:
        load_project(project_dir)
    assert "'other'" in str(exc.value)
    assert "'fx'" in str(exc.value)


@pytest.mark.parametrize(
    ("dbt_version", "label"),
    [
        ("2.0.8", "Fusion 2.0.8"),
        ("2.1.0", "Fusion 2.1.0"),
        ("2.0.0-preview.212", "Fusion 2.0.0-preview.212"),
        ("1.12.6", "dbt Core 1.12.6"),
        ("1.7.0", "dbt Core 1.7.0"),
        ("1.10.0b3", "dbt Core 1.10.0b3"),
        ("weird", "dbt weird"),
    ],
)
def test_producer_label(dbt_version: str, label: str) -> None:
    assert producer_label(dbt_version) == label


def test_project_dir_is_resolved(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = make_project("core_new")
    monkeypatch.chdir(project_dir.parent)
    project = load_project(Path(project_dir.name))
    assert project.project_dir == project_dir.resolve()
