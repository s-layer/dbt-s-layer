"""
The hook on 100 semantic models and 50 exports, with a real dbt engine (HOOK-02).

Writes the project with `scripts/make_scale_project.py`, then runs
`dbt-s-layer pre-commit` the way pre-commit does (a fresh process, the staged
file as argument, the project as working directory): once to create the files,
again after editing one semantic YAML file (timed: under 5 seconds with Fusion),
and a third time, which must change nothing.

dbt Core's own `dbt parse` takes about 5.4 s on this project (start-up bound,
05-RESEARCH finding 1), so with dbt Core only the behaviour is checked, not the
time. Engines come from `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE`, else PATH, as in
test_snowflake_compile.py; each engine's test skips when it is not found.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest
from scripts.make_scale_project import write_project

from tests.test_snowflake_compile import _ENV, _find

if TYPE_CHECKING:
    from pathlib import Path

BUDGET_SECONDS = 5.0
EDITED = "models/marts/fact_007.yml"


def _hook(binary: str, project: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    start = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt_s_layer",
            "pre-commit",
            "--dbt",
            binary,
            "--profiles-dir",
            "profiles",
            EDITED,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=600,
        env=_ENV,
        check=False,
    )
    return result, time.monotonic() - start


@pytest.mark.parametrize("kind", ["Fusion", "dbt Core"])
def test_hook_on_100_semantic_models_and_50_exports(kind: str, tmp_path: Path) -> None:
    binary = _find(kind)
    if binary is None:
        pytest.skip(
            f"no {kind} engine (set DBT_S_LAYER_{'FUSION' if kind == 'Fusion' else 'CORE'})"
        )
    project = tmp_path / "scale"
    write_project(project)

    created, _ = _hook(binary, project)
    assert created.returncode == 1, created.stdout + created.stderr
    out = project / "models" / "semantic"
    assert len(list(out.glob("exp__*.sql"))) == 50
    assert len(list(out.glob("sem__*.sql"))) == 100

    path = project / EDITED
    path.write_text(path.read_text().replace("agg: sum", "agg: max"))
    edited, seconds = _hook(binary, project)
    assert edited.returncode == 1, edited.stdout + edited.stderr
    assert "changed: models/semantic/" in edited.stdout
    if kind == "Fusion":
        assert seconds < BUDGET_SECONDS, f"hook took {seconds:.2f}s"

    again, _ = _hook(binary, project)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "up to date" in again.stdout
