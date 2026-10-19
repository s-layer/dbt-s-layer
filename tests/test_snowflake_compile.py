"""
End to end on a real dbt engine: generated semantic views compile on a Snowflake target (SC4).

Copies a Snowflake fixture project (`snowflake_spec` for Fusion, `snowflake_legacy`
for dbt Core, which fails to compile a new-spec semantic model that lives in a
package: "'metric.fx_dep.store_count' depends on 'semantic_model.fx.stores' which
is not in the graph!", with or without dbt-s-layer's models), installs its packages, and runs
`dbt parse`, `dbt-s-layer generate`, `dbt parse` again, `dbt-s-layer check` and
`dbt compile`. No Snowflake account is needed: dbt Core compiles with
`--no-populate-cache`, Fusion compiles offline (04-RESEARCH §4). This proves the
Jinja, the refs and the DAG edges, not that Snowflake accepts the DDL.

Engines come from `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE` (paths), else from
PATH (`dbtf` or a Fusion `dbt`; a dbt Core `dbt` with the snowflake adapter).
Each engine's test skips when it is not found, and when `dbt deps` cannot
download dbt_semantic_view. CI installs neither engine, so there it skips.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dbt_s_layer._doctor import Engine, parse_engine_version
from dbt_s_layer.cli import app
from dbt_s_layer.project import load_project
from dbt_s_layer.refs import build_ref_map
from dbt_s_layer.semantic_views import build_semantic_views
from tests.conftest import PROJECTS

_TIMEOUT = 600
_ENV = {**os.environ, "DO_NOT_TRACK": "1", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}


def _probe(path: str) -> tuple[Engine | None, str]:
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    if result.returncode != 0:
        return None, ""
    return parse_engine_version(result.stdout, path), result.stdout


def _find(kind: str) -> str | None:
    variable = "DBT_S_LAYER_FUSION" if kind == "Fusion" else "DBT_S_LAYER_CORE"
    explicit = os.environ.get(variable)
    candidates = [explicit] if explicit else (["dbtf", "dbt"] if kind == "Fusion" else ["dbt"])
    for name in candidates:
        path = shutil.which(name)
        if path is None:
            continue
        engine, output = _probe(path)
        if engine is None or engine.kind != kind:
            continue
        if kind == "dbt Core" and "snowflake:" not in output:
            continue
        return path
    return None


def _run(binary: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, *args, "--profiles-dir", str(cwd / "profiles")],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=_ENV,
        check=False,
    )


def _ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"{result.args}\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    ("kind", "project"), [("Fusion", "snowflake_spec"), ("dbt Core", "snowflake_legacy")]
)
def test_generated_semantic_views_compile(kind: str, project: str, tmp_path: Path) -> None:
    binary = _find(kind)
    if binary is None:
        pytest.skip(f"no {kind} engine (set DBT_S_LAYER_{'FUSION' if kind == 'Fusion' else 'CORE'})")
    project_dir = tmp_path / project
    shutil.copytree(
        PROJECTS / project,
        project_dir,
        ignore=shutil.ignore_patterns("target", "logs", "dbt_packages", "package-lock.yml"),
    )
    deps = _run(binary, "deps", cwd=project_dir)
    if deps.returncode != 0:
        pytest.skip(f"`dbt deps` could not install dbt_semantic_view: {deps.stdout[-500:]}")

    _ok(_run(binary, "parse", "--target", "snowflake", cwd=project_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["generate", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "dbt_semantic_view package is not installed" not in result.output
    semantic_dir = project_dir / "models" / "semantic"
    generated = sorted(p.name for p in semantic_dir.glob("sem__*.sql"))
    assert "sem__orders.sql" in generated

    _ok(_run(binary, "parse", "--target", "snowflake", cwd=project_dir))
    result = runner.invoke(app, ["check", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output

    compile_args = ["compile", "--target", "snowflake"]
    if kind == "dbt Core":
        # Core connects to fill its relation cache unless told not to.
        compile_args.append("--no-populate-cache")
    _ok(_run(binary, *compile_args, cwd=project_dir))

    loaded = load_project(project_dir)
    refs = build_ref_map(loaded)
    manifest: dict[str, Any] = json.loads((project_dir / "target" / "manifest.json").read_text())
    nodes = {n["name"]: n for n in manifest["nodes"].values() if n["name"].startswith("sem__")}
    assert sorted(f"{name}.sql" for name in nodes) == generated
    for view in build_semantic_views(loaded).views:
        node = nodes[f"sem__{view.name}"]
        assert node["config"]["materialized"] == "semantic_view"
        expected = sorted(refs[t.semantic_model].node_id for t in view.tables)
        assert sorted(node["depends_on"]["nodes"]) == expected
        compiled = next((project_dir / "target" / "compiled").rglob(f"sem__{view.name}.sql"))
        text = compiled.read_text()
        assert "TABLES (" in text
        assert "{{" not in text
        assert "ref(" not in text
