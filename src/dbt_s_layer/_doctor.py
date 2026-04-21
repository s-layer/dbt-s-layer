"""
Environment diagnostics for dbt-s-layer.

Phase 0 scope (per CONTEXT.md):
- Detects `dbtf` (dbt Fusion) binary on PATH via `shutil.which`
- Reports installed `metricflow` version via `importlib.metadata.version`

Deferred to Phase 1: semantic_manifest.json freshness check.
Deferred to Phase 3: output directory writability check.
"""

from __future__ import annotations

import shutil
import sys
from importlib.metadata import PackageNotFoundError, version

FUSION_INSTALL_URL = "https://docs.getdbt.com/docs/fusion/install-fusion"


def run_doctor() -> int:
    """
    Report environment diagnostics and return an exit code.

    Exit codes:
        0 - all checks passed (dbtf on PATH AND metricflow importable)
        1 - dbtf binary missing (blocking; user must install Fusion)
        2 - metricflow package not installed (dbt-s-layer install is broken)

    When multiple checks fail, the higher-severity code wins (2 > 1 > 0).
    """
    exit_code = 0

    dbtf_path = shutil.which("dbtf")
    if dbtf_path is None:
        print("ERROR: `dbtf` (dbt Fusion) not found on PATH.", file=sys.stdout)
        print(f"Install Fusion: {FUSION_INSTALL_URL}", file=sys.stdout)
        exit_code = max(exit_code, 1)
    else:
        print(f"OK  dbtf found at: {dbtf_path}", file=sys.stdout)

    try:
        mf_version = version("metricflow")
        print(f"OK  metricflow version: {mf_version}", file=sys.stdout)
    except PackageNotFoundError:
        print(
            "ERROR: metricflow is not installed (dbt-s-layer install is broken).",
            file=sys.stdout,
        )
        exit_code = max(exit_code, 2)

    return exit_code
