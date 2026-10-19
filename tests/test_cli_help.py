"""Tests for the dbt-s-layer CLI surface (SC-3, SC-4)."""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from dbt_s_layer import _doctor
from dbt_s_layer.cli import app
from tests.conftest import ARTIFACTS, PROJECTS

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _copy_fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    shutil.copytree(PROJECTS / "legacy_spec", project)
    (project / "target").mkdir()
    for name in ("semantic_manifest.json", "manifest.json"):
        shutil.copyfile(ARTIFACTS / "core_legacy" / name, project / "target" / name)
    return project


def test_root_help_exits_zero() -> None:
    """`dbt-s-layer --help` exits 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_root_help_lists_doctor_subcommand() -> None:
    """`dbt-s-layer --help` output mentions the doctor subcommand."""
    result = runner.invoke(app, ["--help"])
    assert "doctor" in result.stdout


def test_root_help_shows_app_description() -> None:
    """`dbt-s-layer --help` output contains the app's help text."""
    result = runner.invoke(app, ["--help"])
    assert "Code-generate dbt models" in result.stdout


def test_doctor_help_exits_zero() -> None:
    """`dbt-s-layer doctor --help` exits 0."""
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0


def test_doctor_help_shows_docstring() -> None:
    """`dbt-s-layer doctor --help` output contains the command's docstring summary."""
    result = runner.invoke(app, ["doctor", "--help"])
    assert "environment diagnostics" in result.stdout.lower()


@pytest.mark.parametrize("code", [0, 1, 2])
def test_doctor_command_exits_with_run_doctor_code(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """`dbt-s-layer doctor` exits with whatever run_doctor() returns."""

    def fake(project_dir: Path | None = None, output_dir: Path | None = None) -> int:
        return code

    monkeypatch.setattr(_doctor, "run_doctor", fake)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == code


def test_doctor_passes_project_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`dbt-s-layer doctor --project-dir DIR` hands DIR to run_doctor()."""
    seen: list[Path | None] = []

    def fake(project_dir: Path | None = None, output_dir: Path | None = None) -> int:
        seen.append(project_dir)
        return 0

    monkeypatch.setattr(_doctor, "run_doctor", fake)
    assert runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    assert seen == [tmp_path, None]


def test_doctor_help_lists_project_dir() -> None:
    """`dbt-s-layer doctor --help` documents --project-dir."""
    # Rich colours help output in CI and splits the option name with ANSI codes.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", runner.invoke(app, ["doctor", "--help"]).stdout)
    assert "--project-dir" in plain


def test_doctor_project_dir_end_to_end(tmp_path: Path) -> None:
    """A real project with fresh artifacts gets an OK artifacts line and exit 0 or 1."""
    project = _copy_fixture_project(tmp_path)
    result = runner.invoke(app, ["doctor", "--project-dir", str(project)])
    # The engine check depends on this machine's PATH; the artifact check does not.
    assert "OK  artifacts: fresh (dbt Core 1.12.6)" in result.stdout
