"""Tests for the semantic YAML scan and artifact checks (CLI-05)."""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from dbt_s_layer.errors import ArtifactNotFoundError, DbtSLayerError, StaleArtifactError
from dbt_s_layer.staleness import check_artifacts, scan_semantic_yaml
from tests.conftest import ARTIFACTS

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import MakeProject

NEW_SPEC_MODEL = """\
models:
  - name: orders
    semantic_model:
      enabled: true
"""
PLAIN_MODEL_DOCS = """\
models:
  - name: orders
    description: Orders.
    columns:
      - name: order_id
"""
SOURCES = """\
sources:
  - name: raw
    tables:
      - name: orders
"""


def _project(tmp_path: Path, files: dict[str, str], dbt_project: str = "name: fx\n") -> Path:
    (tmp_path / "dbt_project.yml").write_text(dbt_project)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _rel(project_dir: Path, paths: tuple[Path, ...]) -> list[str]:
    return [p.relative_to(project_dir).as_posix() for p in paths]


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


# --- scan_semantic_yaml: what counts ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "semantic_models:\n  - name: orders\n    model: ref('orders')\n",
        "metrics:\n  - name: revenue\n    type: simple\n",
        "saved_queries:\n  - name: revenue_by_day\n",
        NEW_SPEC_MODEL,
        # Disabling a semantic model changes the artifact, so the file still counts.
        "models:\n  - name: orders\n    semantic_model:\n      enabled: false\n",
        "models:\n  - name: orders\n    semantic_model: true\n",
    ],
    ids=[
        "semantic_models",
        "metrics",
        "saved_queries",
        "model_semantic_model",
        "disabled",
        "bool",
    ],
)
def test_semantic_file_counts(tmp_path: Path, text: str) -> None:
    project = _project(tmp_path, {"models/x.yml": text})
    scan = scan_semantic_yaml(project)
    assert _rel(project, scan.files) == ["models/x.yml"]
    assert scan.newest_mtime == (project / "models/x.yml").stat().st_mtime


@pytest.mark.parametrize(
    "text",
    [SOURCES, PLAIN_MODEL_DOCS, "", "# only a comment\n", "version: 2\n", "- a\n- b\n"],
    ids=["sources", "plain_models", "empty", "comment", "version_only", "top_level_list"],
)
def test_non_semantic_file_does_not_count(tmp_path: Path, text: str) -> None:
    project = _project(tmp_path, {"models/x.yml": text})
    scan = scan_semantic_yaml(project)
    assert scan.files == ()
    assert scan.newest_mtime is None


def test_yaml_extension_counts(tmp_path: Path) -> None:
    project = _project(tmp_path, {"models/x.yaml": NEW_SPEC_MODEL})
    assert _rel(project, scan_semantic_yaml(project).files) == ["models/x.yaml"]


def test_only_model_paths_are_scanned(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {"models/a.yml": NEW_SPEC_MODEL, "other/b.yml": NEW_SPEC_MODEL, "c.yml": NEW_SPEC_MODEL},
    )
    assert _rel(project, scan_semantic_yaml(project).files) == ["models/a.yml"]


def test_model_paths_from_dbt_project(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "models/a.yml": NEW_SPEC_MODEL,
            "m1/b.yml": NEW_SPEC_MODEL,
            "m2/sub/c.yml": NEW_SPEC_MODEL,
        },
        dbt_project="name: fx\nmodel-paths: [m1, m2, missing]\n",
    )
    assert _rel(project, scan_semantic_yaml(project).files) == ["m1/b.yml", "m2/sub/c.yml"]


def test_newest_mtime_is_the_newest_semantic_file(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {"models/a.yml": NEW_SPEC_MODEL, "models/b.yml": NEW_SPEC_MODEL, "models/s.yml": SOURCES},
    )
    _touch(project / "models/a.yml", 1_000)
    _touch(project / "models/b.yml", 2_000)
    _touch(project / "models/s.yml", 9_000)
    scan = scan_semantic_yaml(project)
    assert scan.newest_mtime == 2_000
    assert scan.newest_file == project / "models/b.yml"


def test_unparseable_yaml_is_skipped_with_a_warning(tmp_path: Path) -> None:
    project = _project(
        tmp_path, {"models/bad.yml": "models: [unclosed\n", "models/ok.yml": NEW_SPEC_MODEL}
    )
    scan = scan_semantic_yaml(project)
    assert _rel(project, scan.files) == ["models/ok.yml"]
    assert len(scan.warnings) == 1
    assert "models/bad.yml" in scan.warnings[0]
    assert "could not be read" in scan.warnings[0]


# --- scan_semantic_yaml: declared semantic models ---------------------------


def test_declared_semantic_models(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "models/new.yml": """\
models:
  - name: Orders
    semantic_model:
      enabled: true
  - name: customers
    latest_version: 2
    versions:
      - v: 2
    semantic_model: true
  - name: renamed_model
    semantic_model:
      name: renamed_sm
  - name: off
    semantic_model:
      enabled: false
  - name: plain
""",
            "models/legacy.yml": """\
semantic_models:
  - name: legacy_sm
    model: ref('x')
  - name: legacy_off
    model: ref('y')
    config:
      enabled: false
""",
        },
    )
    declared = {d.name: d for d in scan_semantic_yaml(project).declared_semantic_models}
    assert set(declared) == {"orders", "customers", "renamed_sm", "legacy_sm"}
    assert declared["customers"].versioned is True
    assert declared["orders"].versioned is False
    assert declared["legacy_sm"].legacy is True
    assert declared["orders"].legacy is False
    assert declared["orders"].file == project / "models/new.yml"


# --- check_artifacts --------------------------------------------------------


def test_fresh_core_artifacts_warn_about_the_versioned_model(make_project: MakeProject) -> None:
    result = check_artifacts(make_project("core_new"))
    assert result.project.producer == "dbt Core 1.12.6"
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "'customers'" in warning
    assert "models/marts/customers.yml" in warning
    assert "dbt Core 1.12.6" in warning
    assert "versioned" in warning


@pytest.mark.parametrize("set_name", ["fusion_new", "core_legacy"])
def test_fresh_artifacts_without_warnings(set_name: str, make_project: MakeProject) -> None:
    assert check_artifacts(make_project(set_name)).warnings == ()


def test_fusion_on_legacy_spec_warns(make_project: MakeProject) -> None:
    # Fusion ignores legacy-spec semantic models. Simulate its artifacts for the
    # legacy project: Fusion's set without `order_items` (and its metric), and
    # without `customers`, which the legacy project does not declare.
    project_dir = make_project("core_legacy")
    target = project_dir / "target"
    semantic: dict[str, Any] = json.loads(
        (ARTIFACTS / "fusion_new" / "semantic_manifest.json").read_text()
    )
    semantic["semantic_models"] = [
        sm for sm in semantic["semantic_models"] if sm["name"] not in {"order_items", "customers"}
    ]
    semantic["metrics"] = [
        m for m in semantic["metrics"] if m["name"] not in {"items_sold", "cust_total"}
    ]
    (target / "semantic_manifest.json").write_text(json.dumps(semantic))
    shutil.copyfile(ARTIFACTS / "fusion_new" / "manifest.json", target / "manifest.json")

    warnings = check_artifacts(project_dir).warnings
    assert len(warnings) == 1
    assert "'order_items'" in warnings[0]
    assert "Fusion 2.0.8" in warnings[0]
    assert "legacy" in warnings[0]
    assert "dbt Core" in warnings[0]


def test_semantic_yaml_newer_than_artifact_is_stale(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    manifest_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    _touch(project_dir / "models/marts/orders.yml", manifest_mtime + 10)
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    message = str(exc.value)
    assert "models/marts/orders.yml" in message
    assert "`dbt parse`" in message
    assert "semantic_manifest.json" in message


def test_equal_mtime_is_fresh(make_project: MakeProject) -> None:
    project_dir = make_project("core_legacy")
    manifest_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    _touch(project_dir / "models/marts/orders.yml", manifest_mtime)
    assert check_artifacts(project_dir).warnings == ()


def test_newer_non_semantic_yaml_is_not_stale(make_project: MakeProject) -> None:
    project_dir = make_project("core_legacy")
    manifest_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    sources = project_dir / "models" / "sources.yml"
    sources.write_text(SOURCES)
    _touch(sources, manifest_mtime + 10)
    _touch(project_dir / "models/marts/time_spine.yml", manifest_mtime + 10)
    assert check_artifacts(project_dir).warnings == ()


def test_missing_artifact(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    (project_dir / "target" / "semantic_manifest.json").unlink()
    with pytest.raises(ArtifactNotFoundError) as exc:
        check_artifacts(project_dir)
    assert "`dbt parse`" in str(exc.value)


def test_unreadable_yaml_warning_is_reported(make_project: MakeProject) -> None:
    project_dir = make_project("core_legacy")
    bad = project_dir / "models" / "bad.yml"
    bad.write_text("models: [unclosed\n")
    manifest_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    _touch(bad, manifest_mtime - 10)
    warnings = check_artifacts(project_dir).warnings
    assert len(warnings) == 1
    assert "models/bad.yml" in warnings[0]


# --- review round 1: removals, dbt_project.yml, other resource paths ----------


def _age(project_dir: Path, rel: str) -> None:
    """Make a project file older than the artifact, isolating a non-mtime check."""
    artifact_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    _touch(project_dir / rel, artifact_mtime - 100)


def test_removed_semantic_model_yaml_is_stale(make_project: MakeProject) -> None:
    # Dropping the `semantic_models:` block leaves no newer file to compare, but
    # the artifact still has `order_items`.
    project_dir = make_project("core_legacy")
    path = project_dir / "models/marts/order_items.yml"
    path.write_text("models:\n  - name: order_items\n    config:\n      alias: Order_Items\n")
    _age(project_dir, "models/marts/order_items.yml")
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    message = str(exc.value)
    assert "semantic model 'order_items'" in message
    assert "`dbt parse`" in message


def test_deleted_semantic_yaml_file_is_stale(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    (project_dir / "models/marts/order_items.yml").unlink()
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    assert "'order_items'" in str(exc.value)


def test_disabled_semantic_model_still_in_artifact_is_stale(make_project: MakeProject) -> None:
    project_dir = make_project("core_new")
    path = project_dir / "models/marts/order_items.yml"
    path.write_text(path.read_text().replace("enabled: true", "enabled: false"))
    _age(project_dir, "models/marts/order_items.yml")
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    assert "'order_items'" in str(exc.value)


def test_removed_saved_query_is_stale(make_project: MakeProject) -> None:
    project_dir = make_project("core_legacy")
    path = project_dir / "models/marts/orders.yml"
    text = path.read_text()
    path.write_text(text[: text.index("saved_queries:")])
    _age(project_dir, "models/marts/orders.yml")
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    assert "saved query 'revenue_by_day'" in str(exc.value)


def test_undeclared_semantic_model_from_a_package_is_not_stale(
    make_project: MakeProject,
) -> None:
    # A semantic model from an installed package is declared outside this
    # project's paths, so its absence from the scan is expected.
    project_dir = make_project("core_new")
    manifest_path = project_dir / "target" / "manifest.json"
    raw: dict[str, Any] = json.loads(manifest_path.read_text())
    raw["semantic_models"]["semantic_model.fx.order_items"]["package_name"] = "upstream"
    manifest_path.write_text(json.dumps(raw))
    (project_dir / "models/marts/order_items.yml").unlink()
    warnings = check_artifacts(project_dir).warnings
    assert all("order_items" not in w for w in warnings)


def test_removal_check_skipped_when_yaml_is_unreadable(make_project: MakeProject) -> None:
    # The unreadable file might be the one declaring the semantic model.
    project_dir = make_project("core_legacy")
    path = project_dir / "models/marts/order_items.yml"
    path.write_text("semantic_models: [unclosed\n")
    _age(project_dir, "models/marts/order_items.yml")
    warnings = check_artifacts(project_dir).warnings
    assert len(warnings) == 1
    assert "models/marts/order_items.yml" in warnings[0]


def test_newer_dbt_project_yml_is_stale(make_project: MakeProject) -> None:
    # e.g. `semantic-models: +enabled: false` set in dbt_project.yml.
    project_dir = make_project("core_legacy")
    artifact_mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime
    _touch(project_dir / "dbt_project.yml", artifact_mtime + 10)
    with pytest.raises(StaleArtifactError) as exc:
        check_artifacts(project_dir)
    assert "dbt_project.yml" in str(exc.value)


@pytest.mark.parametrize(
    ("config", "rel"),
    [
        ("", "seeds/props.yml"),
        ("", "snapshots/props.yml"),
        ("", "analyses/props.yml"),
        ("", "macros/props.yml"),
        ("seed-paths: [data]\n", "data/props.yml"),
        ("snapshot-paths: [snaps]\n", "snaps/props.yml"),
    ],
)
def test_other_resource_paths_are_scanned(tmp_path: Path, config: str, rel: str) -> None:
    project = _project(tmp_path, {rel: NEW_SPEC_MODEL}, dbt_project=f"name: fx\n{config}")
    assert _rel(project, scan_semantic_yaml(project).files) == [rel]


def test_scan_error_is_a_named_error(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = make_project("core_new")

    def denied(self: Path, pattern: str) -> object:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(type(project_dir), "rglob", denied)
    with pytest.raises(DbtSLayerError) as exc:
        check_artifacts(project_dir)
    assert "Permission denied" in str(exc.value)
