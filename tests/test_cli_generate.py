"""Tests for `dbt-s-layer generate` and `dbt-s-layer check` (CLI-01, CLI-02, CLI-04, OUTPT-04)."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from dbt_s_layer.cli import app
from dbt_s_layer.output import SCHEMA_FILE, render_output
from dbt_s_layer.project import load_project

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import Result

    from tests.conftest import MakeProject

runner = CliRunner()

EXPORT_FILES = [
    "exp__cumulative_revenue__exp_cumulative.sql",
    "exp__items_by_country__exp_items_by_country.sql",
    "exp__revenue_by_day__exp_revenue_by_day.sql",
    "exp__revenue_by_day__exp_revenue_by_day_view.sql",
    "exp__revenue_by_region_country__exp_region_country.sql",
]
SEM_FILES = [
    "sem__customers.sql",
    "sem__order_items.sql",
    "sem__orders.sql",
    "sem__stores.sql",
]
# core_export is a DuckDB parse, so it gets DuckDB semantic views.
ALL_FILES = sorted([SCHEMA_FILE, *EXPORT_FILES, *SEM_FILES])
NO_DUCKDB_PACKAGE = (
    "WARN semantic views: the duckdb_semantic_views package is not installed (manifest.json "
    "has no macro.duckdb_semantic_views.materialization_semantic_view_duckdb). Add "
    "s-layer/dbt-duckdb-semantic-views to packages.yml and run `dbt deps`; until then "
    "`dbt run` fails on the sem__ models."
)


def _invoke(*args: str | Path) -> Result:
    return runner.invoke(app, [str(a) for a in args])


def _listing(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def _mtimes(directory: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}


def _drop_export(project_dir: Path, saved_query: str) -> None:
    """Remove a saved query's exports from the artifacts, as a re-parse would after a YAML edit."""
    path = project_dir / "target" / "semantic_manifest.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    for sq in raw["saved_queries"]:
        if sq["name"] == saved_query:
            sq["exports"] = []
    path.write_text(json.dumps(raw))


@pytest.fixture
def project_dir(make_project: MakeProject) -> Path:
    return make_project("core_export")


# --- generate -----------------------------------------------------------------


def test_help_lists_the_commands() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    for command in ("generate", "check", "doctor"):
        assert command in result.output


def test_generate_writes_to_models_semantic(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    out = project_dir / "models" / "semantic"
    assert _listing(out) == ALL_FILES
    expected = render_output(load_project(project_dir))
    assert {name: (out / name).read_text() for name in ALL_FILES} == expected
    assert "10 files" in result.output
    assert str(out) in result.output


def test_project_dir_defaults_to_the_working_directory(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project_dir)
    result = _invoke("generate")
    assert result.exit_code == 0, result.output
    assert _listing(project_dir / "models" / "semantic") == ALL_FILES


def test_relative_output_dir_is_relative_to_the_project(
    project_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = _invoke("generate", "--project-dir", project_dir, "--output-dir", "models/gen")
    assert result.exit_code == 0, result.output
    assert _listing(project_dir / "models" / "gen") == ALL_FILES
    assert _listing(elsewhere) == []
    # models/gen is under model-paths, so no warning says otherwise.
    assert "model-paths" not in result.output


def test_absolute_output_dir(project_dir: Path) -> None:
    out = project_dir / "models" / "nested" / "deep"
    result = _invoke("generate", "--project-dir", project_dir, "--output-dir", out)
    assert result.exit_code == 0, result.output
    assert _listing(out) == ALL_FILES


def test_output_dir_outside_model_paths_warns(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir, "--output-dir", "generated")
    assert result.exit_code == 0, result.output
    assert _listing(project_dir / "generated") == ALL_FILES
    assert "WARN" in result.output
    assert "model-paths" in result.output


def test_generate_twice_changes_nothing(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    out = project_dir / "models" / "semantic"
    before = {name: (out / name).read_bytes() for name in ALL_FILES}
    mtimes = _mtimes(out)
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output
    assert {name: (out / name).read_bytes() for name in ALL_FILES} == before
    assert _mtimes(out) == mtimes


def test_generate_removes_stale_models(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    _drop_export(project_dir, "cumulative_revenue")
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert "exp__cumulative_revenue__exp_cumulative.sql" not in _listing(
        project_dir / "models" / "semantic"
    )
    assert "removed" in result.output


def test_stale_artifacts_stop_generate(project_dir: Path) -> None:
    yaml_file = project_dir / "models" / "marts" / "orders.yml"
    future = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime + 60
    os.utime(yaml_file, (future, future))
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 1
    assert "dbt parse" in result.output
    assert "Traceback" not in result.output
    assert not (project_dir / "models" / "semantic").exists()


def test_artifact_warnings_are_printed(project_dir: Path) -> None:
    ghost = project_dir / "models" / "ghost.yml"
    ghost.write_text(
        "semantic_models:\n  - name: ghost\n    model: ref('orders')\n"
        "    entities:\n      - name: g\n        type: primary\n"
    )
    past = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime - 60
    os.utime(ghost, (past, past))
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert "WARN semantic model 'ghost'" in result.output


def test_errors_are_one_line_and_write_nothing(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir, "--adapter", "oracle")
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "oracle" in result.output
    assert "Traceback" not in result.output
    assert not (project_dir / "models" / "semantic").exists()


def test_hand_written_file_stops_generate(project_dir: Path) -> None:
    out = project_dir / "models" / "semantic"
    out.mkdir()
    (out / "exp__mine.sql").write_text("select 1\n")
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 1
    assert "exp__mine.sql" in result.output
    assert _listing(out) == ["exp__mine.sql"]


def test_clash_with_a_user_model_stops_generate(project_dir: Path) -> None:
    (project_dir / "models" / "exp_cumulative.sql").write_text("select 1\n")
    path = project_dir / "target" / "manifest.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    raw["nodes"]["model.fx.exp_cumulative"] = {
        "resource_type": "model",
        "unique_id": "model.fx.exp_cumulative",
        "name": "exp_cumulative",
        "package_name": "fx",
        "schema": "marts",
        "alias": "exp_cumulative",
        "original_file_path": "models/exp_cumulative.sql",
        "config": {"schema": None, "database": None},
    }
    path.write_text(json.dumps(raw))
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 1
    assert "model.fx.exp_cumulative" in result.output
    assert not (project_dir / "models" / "semantic").exists()


# --- dialect ------------------------------------------------------------------


def _generated(project_dir: Path) -> dict[str, str]:
    out = project_dir / "models" / "semantic"
    return {name: (out / name).read_text() for name in _listing(out)}


def test_adapter_flag(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir, "--adapter", "bigquery")
    assert result.exit_code == 0, result.output
    assert _generated(project_dir) == render_output(load_project(project_dir), adapter="bigquery")
    assert _generated(project_dir) != render_output(load_project(project_dir))


def test_target_flag_reads_profiles(project_dir: Path) -> None:
    profiles = project_dir / "profiles"
    result = _invoke(
        "generate",
        "--project-dir",
        project_dir,
        "--profiles-dir",
        profiles,
        "--target",
        "snowflake",
    )
    assert result.exit_code == 0, result.output
    assert _generated(project_dir) == render_output(load_project(project_dir), adapter="snowflake")


def test_adapter_wins_over_target(project_dir: Path) -> None:
    result = _invoke(
        "generate",
        "--project-dir",
        project_dir,
        "--profiles-dir",
        project_dir / "profiles",
        "--target",
        "snowflake",
        "--adapter",
        "bigquery",
    )
    assert result.exit_code == 0, result.output
    assert _generated(project_dir) == render_output(load_project(project_dir), adapter="bigquery")


def test_unknown_target(project_dir: Path) -> None:
    result = _invoke(
        "generate",
        "--project-dir",
        project_dir,
        "--profiles-dir",
        project_dir / "profiles",
        "--target",
        "prod",
    )
    assert result.exit_code == 1
    assert "'duckdb', 'snowflake'" in result.output


# --- check --------------------------------------------------------------------


def test_check_after_generate_passes(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    result = _invoke("check", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def test_check_before_generate_fails_and_writes_nothing(project_dir: Path) -> None:
    result = _invoke("check", "--project-dir", project_dir)
    assert result.exit_code == 1
    for name in ALL_FILES:
        assert name in result.output
    assert "dbt-s-layer generate" in result.output
    assert not (project_dir / "models" / "semantic").exists()


def test_check_reports_hand_edits_and_stale_files(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    out = project_dir / "models" / "semantic"
    edited = out / "exp__revenue_by_day__exp_revenue_by_day.sql"
    edited.write_text(edited.read_text().replace("SUM(amount)", "SUM(amount) * 2"))
    _drop_export(project_dir, "cumulative_revenue")
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    result = _invoke("check", "--project-dir", project_dir)
    assert result.exit_code == 1
    assert "changed: exp__revenue_by_day__exp_revenue_by_day.sql" in result.output
    assert "changed: _semantic_views.yml" in result.output
    assert "stale: exp__cumulative_revenue__exp_cumulative.sql" in result.output
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_check_honours_output_dir_and_adapter(project_dir: Path) -> None:
    args = ("--project-dir", project_dir, "--output-dir", "models/gen", "--adapter", "bigquery")
    assert _invoke("generate", *args).exit_code == 0
    assert _invoke("check", *args).exit_code == 0
    # The default dialect renders different SQL.
    result = _invoke("check", "--project-dir", project_dir, "--output-dir", "models/gen")
    assert result.exit_code == 1


def test_check_on_stale_artifacts(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    yaml_file = project_dir / "models" / "marts" / "orders.yml"
    future = (project_dir / "target" / "semantic_manifest.json").stat().st_mtime + 60
    os.utime(yaml_file, (future, future))
    result = _invoke("check", "--project-dir", project_dir)
    assert result.exit_code == 1
    assert "dbt parse" in result.output


# --- semantic views (Phase 4) -----------------------------------------------------------


def test_generate_prints_render_warnings(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert NO_DUCKDB_PACKAGE in result.output.splitlines()


def test_generate_semantic_views_for_snowflake(project_dir: Path) -> None:
    result = _invoke("generate", "--project-dir", project_dir, "--adapter", "snowflake")
    assert result.exit_code == 0, result.output
    out = project_dir / "models" / "semantic"
    assert _listing(out) == ALL_FILES
    assert "10 files" in result.output
    lines = result.output.splitlines()
    assert any(
        line.startswith("WARN semantic views: the dbt_semantic_view package") for line in lines
    )
    assert any("'revenue_cumulative'" in line and line.startswith("WARN") for line in lines)
    # The same warnings, and no drift, from `check`.
    result = _invoke("check", "--project-dir", project_dir, "--adapter", "snowflake")
    assert result.exit_code == 0, result.output
    assert any(
        line.startswith("WARN semantic views: the dbt_semantic_view package")
        for line in result.output.splitlines()
    )


def test_a_semantic_view_error_is_a_warning(make_project: MakeProject) -> None:
    # Review round 1: one semantic model Snowflake cannot hold does not stop the rest.
    project_dir = make_project("fusion_export")
    path = project_dir / "target" / "semantic_manifest.json"
    raw: dict[str, Any] = json.loads(path.read_text())
    sm = next(s for s in raw["semantic_models"] if s["name"] == "orders")
    status = next(d for d in sm["dimensions"] if d["name"] == "status")
    sm["dimensions"].append({**status, "name": "amount", "expr": "amount * 100"})
    path.write_text(json.dumps(raw))
    result = _invoke("generate", "--project-dir", project_dir)
    assert result.exit_code == 0, result.output
    assert any(
        line.startswith("WARN semantic views: skipped sem__orders: dimension orders.amount")
        for line in result.output.splitlines()
    )
    listing = _listing(project_dir / "models" / "semantic")
    assert "sem__orders.sql" not in listing
    assert "sem__customers.sql" in listing
    assert "exp__revenue_by_day__exp_revenue_by_day.sql" in listing
    assert _invoke("check", "--project-dir", project_dir).exit_code == 0


def test_check_after_switching_to_snowflake(project_dir: Path) -> None:
    assert _invoke("generate", "--project-dir", project_dir).exit_code == 0
    result = _invoke("check", "--project-dir", project_dir, "--adapter", "snowflake")
    assert result.exit_code == 1
    for name in SEM_FILES:
        assert f"changed: {name}" in result.output
