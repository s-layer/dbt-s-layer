"""Unit tests for dbt_s_layer._doctor."""

from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

import pytest

from dbt_s_layer import _doctor
from dbt_s_layer._doctor import (
    DBT_CORE_INSTALL_URL,
    FUSION_INSTALL_URL,
    detect_engine,
    parse_engine_version,
    run_doctor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FUSION_VERSION_OUTPUT = "dbt 2.0.8\n"
CORE_VERSION_OUTPUT = """Core:
  - installed: 1.12.6
  - latest:    1.12.6 - Up to date!

Plugins:
  - duckdb: 1.11.0 - Up to date!
"""


def _which(paths: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: paths.get(name)


def _version_output(outputs: dict[str, str]) -> Callable[[str], str | None]:
    return lambda path: outputs.get(path)


def _metricflow_ok(_name: str) -> str:
    return "0.213.0"


def _metricflow_missing(name: str) -> str:
    raise PackageNotFoundError(name)


# --- parse_engine_version -------------------------------------------------


def test_parse_fusion_version() -> None:
    """Fusion prints a single `dbt <version>` line."""
    engine = parse_engine_version(FUSION_VERSION_OUTPUT, "/bin/dbt")
    assert engine is not None
    assert engine.kind == "Fusion"
    assert engine.version == (2, 0, 8)


def test_parse_core_version() -> None:
    """Dbt Core prints a `Core:` block with an `installed:` line."""
    engine = parse_engine_version(CORE_VERSION_OUTPUT, "/bin/dbt")
    assert engine is not None
    assert engine.kind == "dbt Core"
    assert engine.version == (1, 12, 6)


def test_parse_core_prerelease_version() -> None:
    """Pre-release suffixes on Core versions are ignored for comparison."""
    engine = parse_engine_version("Core:\n  - installed: 1.13.0b1\n", "/bin/dbt")
    assert engine is not None
    assert engine.version == (1, 13, 0)


def test_parse_unrecognised_output() -> None:
    """Output that is neither Fusion nor Core is not an engine."""
    assert parse_engine_version("something else entirely\n", "/bin/dbt") is None


def test_parse_dbt_1x_single_line_is_not_fusion() -> None:
    """A bare `dbt 1.x` line is not Fusion, which starts at 2.0."""
    assert parse_engine_version("dbt 1.9.0\n", "/bin/dbt") is None


# --- detect_engine --------------------------------------------------------


def test_detect_prefers_dbtf(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `dbtf` binary on PATH wins over `dbt`."""
    monkeypatch.setattr(_doctor.shutil, "which", _which({"dbtf": "/f/dbtf", "dbt": "/c/dbt"}))
    monkeypatch.setattr(
        _doctor,
        "_version_output",
        _version_output({"/f/dbtf": FUSION_VERSION_OUTPUT, "/c/dbt": CORE_VERSION_OUTPUT}),
    )
    engine = detect_engine()
    assert engine is not None
    assert (engine.kind, engine.path) == ("Fusion", "/f/dbtf")


def test_detect_fusion_installed_as_dbt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Fusion installer names the binary `dbt`; `dbtf` is only a shell alias."""
    monkeypatch.setattr(_doctor.shutil, "which", _which({"dbt": "/f/dbt"}))
    monkeypatch.setattr(
        _doctor, "_version_output", _version_output({"/f/dbt": FUSION_VERSION_OUTPUT})
    )
    engine = detect_engine()
    assert engine is not None
    assert (engine.kind, engine.path) == ("Fusion", "/f/dbt")


def test_detect_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dbt Core on PATH as `dbt` is detected."""
    monkeypatch.setattr(_doctor.shutil, "which", _which({"dbt": "/c/dbt"}))
    monkeypatch.setattr(
        _doctor, "_version_output", _version_output({"/c/dbt": CORE_VERSION_OUTPUT})
    )
    engine = detect_engine()
    assert engine is not None
    assert (engine.kind, engine.version) == ("dbt Core", (1, 12, 6))


def test_detect_falls_back_to_dbt_when_dbtf_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `dbtf` whose version cannot be read does not hide a working `dbt`."""
    monkeypatch.setattr(_doctor.shutil, "which", _which({"dbtf": "/x/dbtf", "dbt": "/c/dbt"}))
    monkeypatch.setattr(
        _doctor, "_version_output", _version_output({"/c/dbt": CORE_VERSION_OUTPUT})
    )
    engine = detect_engine()
    assert engine is not None
    assert engine.path == "/c/dbt"


def test_detect_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No engine on PATH returns None."""
    monkeypatch.setattr(_doctor.shutil, "which", _which({}))
    assert detect_engine() is None


def test_version_output_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary that fails or hangs yields no version output instead of raising."""

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="dbt", timeout=1)

    monkeypatch.setattr(_doctor.subprocess, "run", boom)
    assert _doctor._version_output("/c/dbt") is None


# --- run_doctor -----------------------------------------------------------


def _patch_engine(monkeypatch: pytest.MonkeyPatch, output: str | None) -> None:
    paths = {"dbt": "/e/dbt"} if output is not None else {}
    monkeypatch.setattr(_doctor.shutil, "which", _which(paths))
    monkeypatch.setattr(
        _doctor, "_version_output", _version_output({"/e/dbt": output} if output else {})
    )


@pytest.mark.parametrize(
    ("output", "label"),
    [(FUSION_VERSION_OUTPUT, "Fusion 2.0.8"), (CORE_VERSION_OUTPUT, "dbt Core 1.12.6")],
)
def test_doctor_ok_with_either_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output: str,
    label: str,
) -> None:
    """Returns 0 with either engine present and metricflow installed."""
    _patch_engine(monkeypatch, output)
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 0
    assert f"OK  dbt engine: {label} at /e/dbt" in out
    assert "OK  metricflow version: 0.213.0" in out


def test_doctor_no_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 1 and prints both install links when no engine is on PATH."""
    _patch_engine(monkeypatch, None)
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 1
    assert "ERROR" in out
    assert FUSION_INSTALL_URL in out
    assert DBT_CORE_INSTALL_URL in out


def test_doctor_core_too_old(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 1 for dbt Core older than 1.7, which has no semantic_manifest.json."""
    _patch_engine(monkeypatch, "Core:\n  - installed: 1.6.9\n")
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 1
    assert "dbt Core 1.6.9" in out
    assert "1.7" in out


def test_doctor_core_minimum_version_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dbt Core 1.7.0 exactly is accepted."""
    _patch_engine(monkeypatch, "Core:\n  - installed: 1.7.0\n")
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)
    assert run_doctor() == 0


def test_doctor_metricflow_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 2 when metricflow package metadata cannot be read."""
    _patch_engine(monkeypatch, FUSION_VERSION_OUTPUT)
    monkeypatch.setattr(_doctor, "version", _metricflow_missing)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 2
    assert "ERROR: metricflow is not installed" in out


def test_doctor_all_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 2 (highest severity) when the engine and metricflow are both missing."""
    _patch_engine(monkeypatch, None)
    monkeypatch.setattr(_doctor, "version", _metricflow_missing)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 2
    assert FUSION_INSTALL_URL in out
    assert "metricflow" in out
