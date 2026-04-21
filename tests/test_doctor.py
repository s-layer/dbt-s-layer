"""Unit tests for dbt_s_layer._doctor."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

from dbt_s_layer import _doctor
from dbt_s_layer._doctor import FUSION_INSTALL_URL, run_doctor

if TYPE_CHECKING:
    import pytest


def test_doctor_all_ok(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 0 and prints version when both dbtf and metricflow are present."""
    monkeypatch.setattr(_doctor.shutil, "which", lambda _: "/fake/path/dbtf")
    monkeypatch.setattr(_doctor, "version", lambda _: "0.209.0")

    code = run_doctor()

    captured = capsys.readouterr()
    assert code == 0
    assert "OK" in captured.out
    assert "/fake/path/dbtf" in captured.out
    assert "0.209.0" in captured.out


def test_doctor_dbtf_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 1 and prints Fusion install URL when dbtf is not on PATH."""
    monkeypatch.setattr(_doctor.shutil, "which", lambda _: None)
    monkeypatch.setattr(_doctor, "version", lambda _: "0.209.0")

    code = run_doctor()

    captured = capsys.readouterr()
    assert code == 1
    assert "ERROR" in captured.out
    assert FUSION_INSTALL_URL in captured.out


def test_doctor_metricflow_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 2 when metricflow package metadata cannot be read."""
    monkeypatch.setattr(_doctor.shutil, "which", lambda _: "/fake/path/dbtf")

    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(_doctor, "version", _raise)

    code = run_doctor()

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR" in captured.out
    assert "metricflow" in captured.out


def test_doctor_all_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 2 (highest severity) when both dbtf and metricflow are missing."""
    monkeypatch.setattr(_doctor.shutil, "which", lambda _: None)

    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(_doctor, "version", _raise)

    code = run_doctor()

    captured = capsys.readouterr()
    assert code == 2
    assert FUSION_INSTALL_URL in captured.out
    assert "metricflow" in captured.out
