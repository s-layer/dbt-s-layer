"""Shared pytest fixtures for dbt_s_layer test suite."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
ARTIFACTS = FIXTURES / "artifacts"
PROJECTS = FIXTURES / "projects"

# artifact set -> the fixture project it was parsed from
SET_PROJECTS = {
    "fusion_new": "new_spec",
    "core_new": "new_spec",
    "core_legacy": "legacy_spec",
    "fusion_export": "export_spec",
    "core_export": "export_legacy",
    "fusion_snowflake": "snowflake_spec",
    "core_snowflake": "snowflake_spec",
}
# Sets the Phase 1 loader tests run over.
ARTIFACT_SETS = ("fusion_new", "core_new", "core_legacy")
# Sets parsed from the export fixture projects, with a package and versioned models.
EXPORT_SETS = ("fusion_export", "core_export")
# Sets parsed on a Snowflake target with the dbt_semantic_view package installed.
SNOWFLAKE_SETS = ("fusion_snowflake", "core_snowflake")

MakeProject = Callable[[str], Path]


@pytest.fixture(autouse=True)
def no_dbt_target_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's DBT_TARGET_PATH from leaking into target-dir resolution."""
    monkeypatch.delenv("DBT_TARGET_PATH", raising=False)


@pytest.fixture
def make_project(tmp_path: Path) -> MakeProject:
    """
    Copy a fixture project into tmp_path with an artifact set in its `target/`.

    The artifacts are copied after the project files, so they are newer than
    every YAML file in it, as they would be right after `dbt parse`.
    """

    def _make(set_name: str) -> Path:
        project_dir = tmp_path / set_name
        # Leave out what a local `dbt deps` or `dbt parse` leaves behind (all gitignored).
        shutil.copytree(
            PROJECTS / SET_PROJECTS[set_name],
            project_dir,
            ignore=shutil.ignore_patterns("target", "logs", "dbt_packages", "package-lock.yml"),
        )
        target = project_dir / "target"
        target.mkdir()
        for name in ("semantic_manifest.json", "manifest.json"):
            shutil.copyfile(ARTIFACTS / set_name / name, target / name)
        return project_dir

    return _make
