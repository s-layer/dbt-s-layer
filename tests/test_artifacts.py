"""Tests for reading dbt parse artifacts (PARSE-01, PARSE-02)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from metricflow_semantics.errors.error_classes import MetricFlowException
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_s_layer import artifacts
from dbt_s_layer.artifacts import read_dbt_manifest, read_semantic_manifest, resolve_target_dir
from dbt_s_layer.errors import (
    ArtifactInvalidError,
    ArtifactNotFoundError,
    DbtSLayerError,
    ProjectNotFoundError,
)
from tests.conftest import ARTIFACT_SETS, ARTIFACTS

if TYPE_CHECKING:
    from pathlib import Path


def _project(tmp_path: Path, dbt_project: str = "name: fx\nprofile: fx\n") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dbt_project.yml").write_text(dbt_project)
    return tmp_path


def _write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data))
    return path


def _fixture_json(set_name: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ARTIFACTS / set_name / name).read_text())
    return data


# --- resolve_target_dir -----------------------------------------------------


def test_target_dir_defaults_to_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert resolve_target_dir(project) == project / "target"


def test_target_dir_reads_target_path_from_dbt_project(tmp_path: Path) -> None:
    project = _project(tmp_path, "name: fx\ntarget-path: build/out\n")
    assert resolve_target_dir(project) == project / "build" / "out"


def test_target_dir_keeps_absolute_target_path(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    project = _project(tmp_path / "p", f"name: fx\ntarget-path: {elsewhere}\n")
    assert resolve_target_dir(project) == elsewhere


def test_dbt_target_path_env_overrides_dbt_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, "name: fx\ntarget-path: build/out\n")
    monkeypatch.setenv("DBT_TARGET_PATH", "from_env")
    assert resolve_target_dir(project) == project / "from_env"


def test_empty_dbt_target_path_env_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, "name: fx\ntarget-path: build/out\n")
    monkeypatch.setenv("DBT_TARGET_PATH", "")
    assert resolve_target_dir(project) == project / "build" / "out"


def test_target_dir_without_dbt_project_raises(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError) as exc:
        resolve_target_dir(tmp_path)
    assert str(tmp_path / "dbt_project.yml") in str(exc.value)
    assert "not a dbt project" in str(exc.value)


def test_unparseable_dbt_project_raises_named_error(tmp_path: Path) -> None:
    project = _project(tmp_path, "name: [unclosed\n")
    with pytest.raises(ProjectNotFoundError) as exc:
        resolve_target_dir(project)
    assert "dbt_project.yml" in str(exc.value)
    assert "could not be read" in str(exc.value)


def test_errors_share_a_base_class() -> None:
    for error in (ArtifactInvalidError, ArtifactNotFoundError, ProjectNotFoundError):
        assert issubclass(error, DbtSLayerError)


# --- read_semantic_manifest -------------------------------------------------


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_semantic_manifest_loads_and_builds_a_lookup(set_name: str, tmp_path: Path) -> None:
    manifest = read_semantic_manifest(
        ARTIFACTS / set_name / "semantic_manifest.json", project_dir=tmp_path
    )
    # A legacy manifest loaded without metricflow's transformations fails here.
    SemanticManifestLookup(manifest)
    assert "orders" in {sm.name for sm in manifest.semantic_models}


def test_semantic_model_names_are_lowercased(tmp_path: Path) -> None:
    # metricflow's LowerCaseNamesRule runs on load; the ref map keys on its output.
    raw = _fixture_json("core_new", "semantic_manifest.json")
    for sm in raw["semantic_models"]:
        sm["name"] = sm["name"].upper()
    path = _write_json(tmp_path / "semantic_manifest.json", raw)
    manifest = read_semantic_manifest(path, project_dir=tmp_path)
    assert {sm.name for sm in manifest.semantic_models} == {"order_items", "orders"}


def test_missing_semantic_manifest_says_run_dbt_parse(tmp_path: Path) -> None:
    path = tmp_path / "target" / "semantic_manifest.json"
    with pytest.raises(ArtifactNotFoundError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    message = str(exc.value)
    assert str(path) in message
    assert "`dbt parse`" in message
    assert "dbt Core or Fusion" in message
    assert str(tmp_path) in message


def test_semantic_manifest_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "semantic_manifest.json"
    path.write_text('{"semantic_models": [')
    with pytest.raises(ArtifactInvalidError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_semantic_manifest_wrong_shape_has_no_raw_traceback(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "semantic_manifest.json", {"semantic_models": 3})
    with pytest.raises(ArtifactInvalidError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    message = str(exc.value)
    assert str(path) in message
    assert "semantic_models" in message
    assert "PydanticSemanticManifest" not in message
    assert "type=" not in message


def test_semantic_manifest_metricflow_rejects(tmp_path: Path) -> None:
    raw = _fixture_json("core_legacy", "semantic_manifest.json")
    revenue = next(m for m in raw["metrics"] if m["name"] == "revenue")
    revenue["type_params"]["measure"] = {"name": "no_such_measure"}
    path = _write_json(tmp_path / "semantic_manifest.json", raw)
    with pytest.raises(ArtifactInvalidError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    message = str(exc.value)
    assert str(path) in message
    assert "no_such_measure" in message
    assert "revenue" in message


# --- read_dbt_manifest ------------------------------------------------------


@pytest.mark.parametrize("set_name", ARTIFACT_SETS)
def test_dbt_manifest_loads(set_name: str, tmp_path: Path) -> None:
    manifest = read_dbt_manifest(ARTIFACTS / set_name / "manifest.json", project_dir=tmp_path)
    assert manifest.metadata.project_name == "fx"
    assert "model.fx.orders" in manifest.nodes
    assert "semantic_model.fx.orders" in manifest.semantic_models
    orders = manifest.semantic_models["semantic_model.fx.orders"]
    assert orders.model == "ref('orders')"
    assert orders.depends_on.nodes == ("model.fx.orders",)


def test_dbt_manifest_node_fields(tmp_path: Path) -> None:
    manifest = read_dbt_manifest(ARTIFACTS / "fusion_new" / "manifest.json", project_dir=tmp_path)
    customers = manifest.nodes["model.fx.customers.v2"]
    assert (customers.name, customers.package_name, customers.version) == ("customers", "fx", 2)
    assert (customers.database, customers.schema_name, customers.alias) == (
        "ANALYTICS",
        "MARTS",
        "customers_v2",
    )
    assert manifest.nodes["model.fx.order_items"].alias == "Order_Items"
    assert manifest.nodes["model.fx.orders"].version is None


def test_dbt_manifest_keeps_models_seeds_and_snapshots(tmp_path: Path) -> None:
    raw = _fixture_json("core_new", "manifest.json")
    # An untrimmed manifest also carries tests, macros, sources...
    raw["nodes"]["test.fx.not_null_orders_order_id"] = {"resource_type": "test", "name": "t"}
    for kind in ("seed", "snapshot"):
        raw["nodes"][f"{kind}.fx.raw_{kind}"] = {
            "resource_type": kind,
            "unique_id": f"{kind}.fx.raw_{kind}",
            "name": f"raw_{kind}",
            "package_name": "fx",
            "database": "memory",
            "schema": "marts",
            "alias": f"raw_{kind}",
        }
    raw["macros"] = {"macro.fx.m": {"name": "m"}}
    raw["sources"] = {"source.fx.s.t": {"name": "t"}}
    path = _write_json(tmp_path / "manifest.json", raw)
    manifest = read_dbt_manifest(path, project_dir=tmp_path)
    assert set(manifest.nodes) == {
        "model.fx.customers.v2",
        "model.fx.metricflow_time_spine",
        "model.fx.order_items",
        "model.fx.orders",
        "seed.fx.raw_seed",
        "snapshot.fx.raw_snapshot",
    }
    assert manifest.nodes["seed.fx.raw_seed"].resource_type == "seed"


def test_dbt_manifest_saved_queries(tmp_path: Path) -> None:
    manifest = read_dbt_manifest(ARTIFACTS / "core_new" / "manifest.json", project_dir=tmp_path)
    saved = manifest.saved_queries["saved_query.fx.revenue_by_day"]
    assert (saved.name, saved.package_name) == ("revenue_by_day", "fx")


def test_metricflow_transform_failure_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_manifest: object) -> object:
        raise MetricFlowException("transform blew up")

    monkeypatch.setattr(artifacts, "transform_dbt_generated_manifest", boom)
    path = ARTIFACTS / "core_new" / "semantic_manifest.json"
    with pytest.raises(ArtifactInvalidError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)
    assert "transform blew up" in str(exc.value)


def test_metricflow_error_with_empty_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def empty(_manifest: object) -> object:
        raise MetricFlowException("")

    monkeypatch.setattr(artifacts, "SemanticManifestLookup", empty)
    path = ARTIFACTS / "core_new" / "semantic_manifest.json"
    with pytest.raises(ArtifactInvalidError) as exc:
        read_semantic_manifest(path, project_dir=tmp_path)
    assert "MetricFlowException" in str(exc.value)


def test_missing_dbt_manifest_says_run_dbt_parse(tmp_path: Path) -> None:
    path = tmp_path / "target" / "manifest.json"
    with pytest.raises(ArtifactNotFoundError) as exc:
        read_dbt_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)
    assert "`dbt parse`" in str(exc.value)


def test_dbt_manifest_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("not json")
    with pytest.raises(ArtifactInvalidError) as exc:
        read_dbt_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_dbt_manifest_missing_metadata_names_the_field(tmp_path: Path) -> None:
    raw = _fixture_json("core_new", "manifest.json")
    del raw["metadata"]["dbt_version"]
    path = _write_json(tmp_path / "manifest.json", raw)
    with pytest.raises(ArtifactInvalidError) as exc:
        read_dbt_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)
    assert "metadata.dbt_version" in str(exc.value)


def test_dbt_manifest_not_an_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "manifest.json", [1, 2])
    with pytest.raises(ArtifactInvalidError) as exc:
        read_dbt_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)


def test_artifact_that_is_a_directory_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.mkdir()
    with pytest.raises(ArtifactInvalidError) as exc:
        read_dbt_manifest(path, project_dir=tmp_path)
    assert str(path) in str(exc.value)


# --- macros (Phase 4: is dbt_semantic_view installed?) -----------------------


def test_dbt_manifest_macros_are_ids(tmp_path: Path) -> None:
    raw = _fixture_json("core_new", "manifest.json")
    raw["macros"] = {
        "macro.dbt_semantic_view.materialization_semantic_view_snowflake": {"name": "x"},
        "macro.dbt.run_query": {"name": "run_query"},
    }
    manifest = read_dbt_manifest(_write_json(tmp_path / "manifest.json", raw), project_dir=tmp_path)
    assert manifest.macros == frozenset(
        {"macro.dbt_semantic_view.materialization_semantic_view_snowflake", "macro.dbt.run_query"}
    )


def test_dbt_manifest_without_macros(tmp_path: Path) -> None:
    manifest = read_dbt_manifest(ARTIFACTS / "core_new" / "manifest.json", project_dir=tmp_path)
    assert manifest.macros == frozenset()
