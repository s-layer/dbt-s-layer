"""Tests for the dbt-s-layer CLI surface (SC-3, SC-4)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dbt_s_layer import _doctor
from dbt_s_layer.cli import app

runner = CliRunner()


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
    monkeypatch.setattr(_doctor, "run_doctor", lambda: code)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == code
