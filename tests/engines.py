"""Find and run real dbt engines for the compile integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from dbt_s_layer._doctor import Engine, parse_engine_version
from tests.conftest import PROJECTS

if TYPE_CHECKING:
    from pathlib import Path

_TIMEOUT = 600
# No telemetry from the engines under test.
ENV = {**os.environ, "DO_NOT_TRACK": "1", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}


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


def find_engine(kind: str, variable: str, adapter: str) -> str | None:
    """
    The engine in `variable` (a path), else on PATH: `dbtf` or a Fusion `dbt`, or a
    dbt Core `dbt` that has `adapter` installed.
    """
    explicit = os.environ.get(variable)
    candidates = [explicit] if explicit else (["dbtf", "dbt"] if kind == "Fusion" else ["dbt"])
    for name in candidates:
        path = shutil.which(name)
        if path is None:
            continue
        engine, output = _probe(path)
        if engine is None or engine.kind != kind:
            continue
        if kind == "dbt Core" and f"{adapter}:" not in output:
            continue
        return path
    return None


def copy_project(project: str, tmp_path: Path) -> Path:
    """Copy a fixture project, leaving out what `dbt deps` or `dbt parse` left behind."""
    project_dir = tmp_path / project
    shutil.copytree(
        PROJECTS / project,
        project_dir,
        ignore=shutil.ignore_patterns("target", "logs", "dbt_packages", "package-lock.yml"),
    )
    return project_dir


def run(binary: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a dbt command with the project's own profiles."""
    return subprocess.run(
        [binary, *args, "--profiles-dir", str(cwd / "profiles")],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=ENV,
        check=False,
    )


def ok(result: subprocess.CompletedProcess[str]) -> None:
    """Fail the test with the command's output when it failed."""
    assert result.returncode == 0, f"{result.args}\n{result.stdout}\n{result.stderr}"
