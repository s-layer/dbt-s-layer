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
}
ARTIFACT_SETS = tuple(SET_PROJECTS)

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
        shutil.copytree(PROJECTS / SET_PROJECTS[set_name], project_dir)
        target = project_dir / "target"
        target.mkdir()
        for name in ("semantic_manifest.json", "manifest.json"):
            shutil.copyfile(ARTIFACTS / set_name / name, target / name)
        return project_dir

    return _make
