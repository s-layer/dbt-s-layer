"""Unit tests for dbt_s_layer._doctor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from dbt_s_layer import _doctor
from dbt_s_layer._doctor import (
    DBT_CORE_INSTALL_URL,
    FUSION_INSTALL_URL,
    VersionProbeError,
    detect_engines,
    parse_engine_version,
    run_doctor,
)
from dbt_s_layer.cli import app

if TYPE_CHECKING:
    from tests.conftest import MakeProject

# Real `--version` output, captured 2026-10-19 from the builds named.
FUSION_STABLE_OUTPUT = "dbt 2.0.8\n"  # stable/latest channel
FUSION_DEV_OUTPUT = "dbt 2.1.0\n"  # dev channel, v2.1.0.dev20261017
FUSION_PREVIEW_OUTPUT = "dbt-fusion 2.0.0-preview.212\n"  # extended/fallback channel
CORE_OUTPUT = """Core:
  - installed: 1.12.6
  - latest:    1.12.6 - Up to date!

Plugins:
  - duckdb: 1.11.0 - Up to date!
"""
# Shapes printed by tools that are not supported engines.
CORE_PRE_1_0_OUTPUT = "installed version: 0.21.0\n   latest version: 0.21.0\n"
CLOUD_CLI_OUTPUT = "dbt Cloud CLI - 0.38.22 (abc123 2024-01-01T00:00:00Z)\n"

VersionFn = Callable[[str], str]


def _which(paths: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: paths.get(name)


def _versions(outputs: dict[str, str | VersionProbeError]) -> VersionFn:
    def fn(path: str) -> str:
        out = outputs[path]
        if isinstance(out, VersionProbeError):
            raise out
        return out

    return fn


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    paths: dict[str, str],
    outputs: dict[str, str | VersionProbeError],
) -> None:
    monkeypatch.setattr(_doctor.shutil, "which", _which(paths))
    monkeypatch.setattr(_doctor, "_version_output", _versions(outputs))


def _metricflow_ok(_name: str) -> str:
    return "0.213.0"


def _metricflow_missing(name: str) -> str:
    raise PackageNotFoundError(name)


# --- parse_engine_version -------------------------------------------------


@pytest.mark.parametrize(
    ("output", "version"),
    [
        (FUSION_STABLE_OUTPUT, (2, 0, 8)),
        (FUSION_DEV_OUTPUT, (2, 1, 0)),
        (FUSION_PREVIEW_OUTPUT, (2, 0, 0)),
    ],
)
def test_parse_fusion_versions(output: str, version: tuple[int, int, int]) -> None:
    """Every Fusion release channel's output is recognised as Fusion."""
    engine = parse_engine_version(output, "/bin/dbt")
    assert engine is not None
    assert engine.kind == "Fusion"
    assert engine.version == version


def test_parse_core_version() -> None:
    """Dbt Core prints a `Core:` block with an `installed:` line."""
    engine = parse_engine_version(CORE_OUTPUT, "/bin/dbt")
    assert engine is not None
    assert engine.kind == "dbt Core"
    assert engine.version == (1, 12, 6)


def test_parse_core_prerelease_version() -> None:
    """Pre-release suffixes on Core versions are ignored for comparison."""
    engine = parse_engine_version("Core:\n  - installed: 1.13.0b1\n", "/bin/dbt")
    assert engine is not None
    assert engine.version == (1, 13, 0)


def test_parse_core_pre_1_0_version() -> None:
    """Pre-1.0 Core's `installed version:` form is still recognised as Core."""
    engine = parse_engine_version(CORE_PRE_1_0_OUTPUT, "/bin/dbt")
    assert engine is not None
    assert (engine.kind, engine.version) == ("dbt Core", (0, 21, 0))


@pytest.mark.parametrize("output", ["something else\n", "dbt 1.9.0\n", CLOUD_CLI_OUTPUT])
def test_parse_unrecognised_output(output: str) -> None:
    """Output that is not Fusion (2.x) or Core is not an engine."""
    assert parse_engine_version(output, "/bin/dbt") is None


# --- detect_engines -------------------------------------------------------


def test_detect_reports_every_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both `dbtf` and `dbt` are reported, in that order."""
    _patch(
        monkeypatch,
        {"dbtf": "/f/dbtf", "dbt": "/c/dbt"},
        {"/f/dbtf": FUSION_STABLE_OUTPUT, "/c/dbt": CORE_OUTPUT},
    )
    found = detect_engines()
    assert [(e.kind, e.path) for e in found.engines] == [
        ("Fusion", "/f/dbtf"),
        ("dbt Core", "/c/dbt"),
    ]
    assert found.rejected == ()


def test_detect_fusion_installed_as_dbt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Fusion installer names the binary `dbt`; `dbtf` is only a shell alias."""
    _patch(monkeypatch, {"dbt": "/f/dbt"}, {"/f/dbt": FUSION_STABLE_OUTPUT})
    found = detect_engines()
    assert [(e.kind, e.path) for e in found.engines] == [("Fusion", "/f/dbt")]


def test_detect_same_binary_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """`dbtf` and `dbt` resolving to one file are reported once."""
    _patch(monkeypatch, {"dbtf": "/f/dbt", "dbt": "/f/dbt"}, {"/f/dbt": FUSION_STABLE_OUTPUT})
    assert len(detect_engines().engines) == 1


def test_detect_records_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary whose version probe fails is rejected with the reason, not hidden."""
    _patch(
        monkeypatch,
        {"dbtf": "/x/dbtf", "dbt": "/c/dbt"},
        {"/x/dbtf": VersionProbeError("timed out after 30s"), "/c/dbt": CORE_OUTPUT},
    )
    found = detect_engines()
    assert [e.path for e in found.engines] == ["/c/dbt"]
    assert [(r.path, r.reason) for r in found.rejected] == [("/x/dbtf", "timed out after 30s")]


def test_detect_records_unrecognised_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `dbt` that is neither engine (e.g. the dbt Cloud CLI) is rejected with its output."""
    _patch(monkeypatch, {"dbt": "/cloud/dbt"}, {"/cloud/dbt": CLOUD_CLI_OUTPUT})
    found = detect_engines()
    assert found.engines == ()
    assert len(found.rejected) == 1
    assert found.rejected[0].path == "/cloud/dbt"
    assert "dbt Cloud CLI - 0.38.22" in found.rejected[0].reason


def test_detect_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing on PATH gives an empty result."""
    _patch(monkeypatch, {}, {})
    found = detect_engines()
    assert (found.engines, found.rejected) == ((), ())


# --- _version_output ------------------------------------------------------


def _fake_run(result: subprocess.CompletedProcess[str] | BaseException) -> object:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def test_version_output_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful probe returns stdout."""
    ok = subprocess.CompletedProcess(["dbt", "--version"], 0, stdout="dbt 2.0.8\n", stderr="")
    monkeypatch.setattr(_doctor.subprocess, "run", _fake_run(ok))
    assert _doctor._version_output("/c/dbt") == "dbt 2.0.8\n"


def test_version_output_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero exit raises VersionProbeError naming the code."""
    bad = subprocess.CompletedProcess(["dbt", "--version"], 3, stdout="", stderr="boom")
    monkeypatch.setattr(_doctor.subprocess, "run", _fake_run(bad))
    with pytest.raises(VersionProbeError, match="exited with code 3"):
        _doctor._version_output("/c/dbt")


def test_version_output_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hang raises VersionProbeError saying it timed out."""
    monkeypatch.setattr(
        _doctor.subprocess, "run", _fake_run(subprocess.TimeoutExpired(cmd="dbt", timeout=30))
    )
    with pytest.raises(VersionProbeError, match="timed out"):
        _doctor._version_output("/c/dbt")


@pytest.mark.parametrize("exc", [PermissionError("denied"), FileNotFoundError("dangling")])
def test_version_output_oserror(monkeypatch: pytest.MonkeyPatch, exc: OSError) -> None:
    """An unrunnable binary raises VersionProbeError instead of crashing doctor."""
    monkeypatch.setattr(_doctor.subprocess, "run", _fake_run(exc))
    with pytest.raises(VersionProbeError, match="could not run"):
        _doctor._version_output("/c/dbt")


def test_version_output_does_not_use_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe passes check=False so a non-zero exit is reported, not raised raw."""
    seen: dict[str, object] = {}

    def run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(["dbt"], 0, stdout="dbt 2.0.8\n", stderr="")

    monkeypatch.setattr(_doctor.subprocess, "run", run)
    _doctor._version_output("/c/dbt")
    assert seen["check"] is False
    assert seen["timeout"] == _doctor._VERSION_TIMEOUT_SECONDS


# --- run_doctor -----------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "label"),
    [(FUSION_STABLE_OUTPUT, "Fusion 2.0.8"), (CORE_OUTPUT, "dbt Core 1.12.6")],
)
def test_doctor_ok_with_either_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output: str,
    label: str,
) -> None:
    """Returns 0 with either engine present and metricflow installed."""
    _patch(monkeypatch, {"dbt": "/e/dbt"}, {"/e/dbt": output})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 0
    assert f"OK  dbt engine: {label} at /e/dbt" in out
    assert "OK  metricflow version: 0.213.0" in out


def test_doctor_fusion_only_notes_legacy_spec(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With only Fusion available, doctor says legacy-spec projects need dbt Core."""
    _patch(monkeypatch, {"dbtf": "/f/dbtf"}, {"/f/dbtf": FUSION_STABLE_OUTPUT})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    assert run_doctor() == 0
    out = capsys.readouterr().out
    assert "NOTE" in out
    assert "Legacy-spec projects" in out
    assert DBT_CORE_INSTALL_URL in out


def test_doctor_no_legacy_note_when_core_present(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With a supported Core next to Fusion, there is nothing to note."""
    _patch(
        monkeypatch,
        {"dbtf": "/f/dbtf", "dbt": "/c/dbt"},
        {"/f/dbtf": FUSION_STABLE_OUTPUT, "/c/dbt": CORE_OUTPUT},
    )
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    assert run_doctor() == 0
    out = capsys.readouterr().out
    assert "OK  dbt engine: Fusion 2.0.8 at /f/dbtf" in out
    assert "OK  dbt engine: dbt Core 1.12.6 at /c/dbt" in out
    assert "NOTE" not in out


def test_doctor_no_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 1 and prints both install links when no engine is on PATH."""
    _patch(monkeypatch, {}, {})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 1
    assert "ERROR: no supported dbt engine" in out
    assert FUSION_INSTALL_URL in out
    assert DBT_CORE_INSTALL_URL in out


def test_doctor_names_rejected_binary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `dbt` on PATH that is not an engine is named with why, not reported as absent."""
    _patch(monkeypatch, {"dbt": "/cloud/dbt"}, {"/cloud/dbt": CLOUD_CLI_OUTPUT})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 1
    assert "WARN found /cloud/dbt" in out
    assert "dbt Cloud CLI" in out


@pytest.mark.parametrize(
    "output", ["Core:\n  - installed: 1.6.9\n", CORE_PRE_1_0_OUTPUT], ids=["1.6", "0.21"]
)
def test_doctor_core_too_old(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output: str,
) -> None:
    """Returns 1 for dbt Core older than 1.7, which has no semantic_manifest.json."""
    _patch(monkeypatch, {"dbt": "/c/dbt"}, {"/c/dbt": output})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 1
    assert "too old" in out
    assert ">= 1.7" in out


def test_doctor_core_minimum_version_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dbt Core 1.7.0 exactly is accepted."""
    _patch(monkeypatch, {"dbt": "/c/dbt"}, {"/c/dbt": "Core:\n  - installed: 1.7.0\n"})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)
    assert run_doctor() == 0


def test_doctor_metricflow_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Returns 2 when metricflow package metadata cannot be read."""
    _patch(monkeypatch, {"dbt": "/e/dbt"}, {"/e/dbt": FUSION_STABLE_OUTPUT})
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
    _patch(monkeypatch, {}, {})
    monkeypatch.setattr(_doctor, "version", _metricflow_missing)

    code = run_doctor()

    out = capsys.readouterr().out
    assert code == 2
    assert FUSION_INSTALL_URL in out
    assert "metricflow" in out


# --- doctor --project-dir (CLI-05, CLI-03 Phase 1) ---------------------------


def _engines_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"dbt": "/c/dbt"}, {"/c/dbt": CORE_OUTPUT})
    monkeypatch.setattr(_doctor, "version", _metricflow_ok)


def test_doctor_without_project_dir_skips_artifact_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor() == 0
    assert "artifacts" not in capsys.readouterr().out


def test_doctor_project_fresh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    # core_duckdb, not core_legacy: a DuckDB project needs the duckdb_semantic_views package.
    assert run_doctor(make_project("core_duckdb")) == 0
    out = capsys.readouterr().out
    assert "OK  artifacts: fresh (dbt Core 1.12.6)" in out
    assert "WARN" not in out


def test_doctor_project_fresh_with_warning_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(make_project("core_new")) == 0
    out = capsys.readouterr().out
    assert "OK  artifacts: fresh (dbt Core 1.12.6)" in out
    assert "WARN semantic model 'customers'" in out


def test_doctor_project_stale(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("core_legacy")
    mtime = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime + 10
    os.utime(project_dir / "models/marts/orders.yml", (mtime, mtime))
    assert run_doctor(project_dir) == 1
    out = capsys.readouterr().out
    assert "ERROR artifacts:" in out
    assert "models/marts/orders.yml" in out
    assert "OK  artifacts" not in out


def test_doctor_project_missing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("core_new")
    shutil.rmtree(project_dir / "target")
    assert run_doctor(project_dir) == 1
    out = capsys.readouterr().out
    assert "ERROR artifacts:" in out
    assert "`dbt parse`" in out


def test_doctor_not_a_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(tmp_path) == 1
    assert "not a dbt project" in capsys.readouterr().out


def test_doctor_skips_project_check_without_metricflow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    # Loading artifacts imports metricflow, so the check cannot run without it.
    _patch(monkeypatch, {"dbt": "/c/dbt"}, {"/c/dbt": CORE_OUTPUT})
    monkeypatch.setattr(_doctor, "version", _metricflow_missing)

    def must_not_run(_project_dir: Path) -> int:
        raise AssertionError("project check ran without metricflow")

    monkeypatch.setattr(_doctor, "_check_project", must_not_run)
    assert run_doctor(make_project("core_new")) == 2
    out = capsys.readouterr().out
    assert "SKIP artifacts" in out
    assert "metricflow" in out


# --- output directory (CLI-03) ------------------------------------------------


def _refuse_access(locked: Path) -> Callable[[object, int], bool]:
    def access(path: object, mode: int) -> bool:
        return Path(str(path)) != locked

    return access


def test_doctor_output_dir_writable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("core_export")
    out = project_dir / "models" / "semantic"
    out.mkdir()
    assert run_doctor(project_dir) == 0
    assert f"OK  output directory: {out} is writable" in capsys.readouterr().out


def test_doctor_output_dir_not_created_yet(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("core_export")
    assert run_doctor(project_dir, Path("models/gen/deep")) == 0
    out = capsys.readouterr().out
    assert f"OK  output directory: {project_dir / 'models/gen/deep'} does not exist yet" in out
    assert str(project_dir / "models") in out
    assert not (project_dir / "models" / "gen").exists()


@pytest.mark.parametrize("existing", [True, False])
def test_doctor_output_dir_not_writable(
    existing: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    # Tests may run as root, which os.access never refuses, so refusal is simulated.
    _engines_ok(monkeypatch)
    project_dir = make_project("core_export")
    out = project_dir / "models" / "semantic"
    if existing:
        out.mkdir()
    locked = out if existing else project_dir / "models"
    monkeypatch.setattr(_doctor.os, "access", _refuse_access(locked))
    assert run_doctor(project_dir) == 1
    text = capsys.readouterr().out
    assert "ERROR output directory:" in text
    assert str(locked) in text


def test_doctor_output_dir_parent_not_writable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    # Files are staged beside the output directory, so its parent must be writable too.
    _engines_ok(monkeypatch)
    project_dir = make_project("core_export")
    (project_dir / "models" / "semantic").mkdir()
    locked = project_dir / "models"
    monkeypatch.setattr(_doctor.os, "access", _refuse_access(locked))
    assert run_doctor(project_dir) == 1
    assert "ERROR output directory:" in capsys.readouterr().out


def test_doctor_output_dir_is_a_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("core_export")
    (project_dir / "models" / "semantic").write_text("")
    assert run_doctor(project_dir) == 1
    assert "is not a directory" in capsys.readouterr().out


def test_doctor_cli_passes_output_dir(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[object, object]] = []

    def fake(project_dir: object = None, output_dir: object = None) -> int:
        seen.append((project_dir, output_dir))
        return 0

    monkeypatch.setattr(_doctor, "run_doctor", fake)
    project_dir = make_project("core_export")
    result = CliRunner().invoke(
        app, ["doctor", "--project-dir", str(project_dir), "--output-dir", "models/x"]
    )
    assert result.exit_code == 0, result.output
    assert seen == [(project_dir, Path("models/x"))]


# --- dbt_semantic_view (Phase 4) --------------------------------------------------------


def test_doctor_reports_missing_semantic_view_package_on_snowflake(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(make_project("fusion_export")) == 0
    out = capsys.readouterr().out
    assert "WARN dbt_semantic_view: not installed" in out
    assert "packages.yml" in out


def test_doctor_reports_installed_semantic_view_package(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("fusion_export")
    path = project_dir / "target" / "manifest.json"
    raw = json.loads(path.read_text())
    raw["macros"] = {
        "macro.dbt_semantic_view.materialization_semantic_view_snowflake": {"name": "m"}
    }
    path.write_text(json.dumps(raw))
    assert run_doctor(project_dir) == 0
    assert "OK  dbt_semantic_view: installed" in capsys.readouterr().out


def test_doctor_says_nothing_about_the_package_off_snowflake(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(make_project("core_export")) == 0
    assert "dbt_semantic_view" not in capsys.readouterr().out


# --- dbt-databricks metric views (Phase 6) ----------------------------------------------


def _on_databricks(project_dir: Path, macros: dict[str, Any]) -> None:
    path = project_dir / "target" / "manifest.json"
    raw = json.loads(path.read_text())
    raw["metadata"]["adapter_type"] = "databricks"
    raw["macros"] = macros
    path.write_text(json.dumps(raw))


def test_doctor_reports_a_missing_metric_view_materialization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("fusion_export")
    _on_databricks(project_dir, {})
    assert run_doctor(project_dir) == 0
    out = capsys.readouterr().out
    assert "WARN dbt-databricks metric views: no metric_view materialization" in out
    assert ">= 1.12.0" in out


def test_doctor_reports_the_metric_view_materialization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    project_dir = make_project("fusion_export")
    _on_databricks(
        project_dir, {"macro.dbt_databricks.materialization_metric_view_databricks": {"name": "m"}}
    )
    assert run_doctor(project_dir) == 0
    assert "OK  dbt-databricks metric views: installed" in capsys.readouterr().out


# --- duckdb_semantic_views (Phase 7) ----------------------------------------------------


def test_doctor_reports_a_missing_duckdb_package(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(make_project("core_export")) == 0
    out = capsys.readouterr().out
    assert "WARN duckdb_semantic_views: not installed" in out
    assert "s-layer/dbt-duckdb-semantic-views to packages.yml" in out


def test_doctor_reports_the_duckdb_package(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_project: MakeProject,
) -> None:
    _engines_ok(monkeypatch)
    assert run_doctor(make_project("core_duckdb")) == 0
    assert "OK  duckdb_semantic_views: installed" in capsys.readouterr().out
