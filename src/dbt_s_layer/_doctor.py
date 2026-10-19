"""
Environment diagnostics for dbt-s-layer.

Phase 0 scope (per CONTEXT.md, revised at the Phase 0 close-out):
- Detects a dbt engine that can write `semantic_manifest.json`: dbt Fusion
  (as `dbtf`, or as `dbt` 2.x, which is how the Fusion installer names it) or
  dbt Core >= 1.7. dbt Core 1.12 parses both the legacy and the new YAML spec;
  Fusion only parses the new one.
- Reports installed `metricflow` version via `importlib.metadata.version`

Deferred to Phase 1: semantic_manifest.json freshness check.
Deferred to Phase 3: output directory writability check.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

FUSION_INSTALL_URL = "https://docs.getdbt.com/docs/fusion/install-fusion"
DBT_CORE_INSTALL_URL = "https://docs.getdbt.com/docs/core/installation-overview"

# dbt Core gained semantic_manifest.json in 1.7 (semantic models GA).
MIN_DBT_CORE_VERSION = (1, 7, 0)
_VERSION_TIMEOUT_SECONDS = 30

_FUSION_RE = re.compile(r"^dbt (\d+)\.(\d+)\.(\d+)", re.MULTILINE)
_CORE_RE = re.compile(r"^Core:\s*\n\s*-\s*installed:\s*(\d+)\.(\d+)\.(\d+)", re.MULTILINE)

EngineKind = Literal["Fusion", "dbt Core"]


@dataclass(frozen=True)
class Engine:
    """A dbt engine found on PATH."""

    kind: EngineKind
    version: tuple[int, int, int]
    path: str

    @property
    def label(self) -> str:
        """Human-readable name and version, e.g. `Fusion 2.0.8`."""
        return f"{self.kind} {'.'.join(map(str, self.version))}"


def parse_engine_version(output: str, path: str) -> Engine | None:
    """Classify `<binary> --version` output as Fusion or dbt Core, or None."""
    fusion = _FUSION_RE.search(output)
    if fusion is not None:
        major, minor, patch = (int(g) for g in fusion.groups())
        if major >= 2:
            return Engine("Fusion", (major, minor, patch), path)
    core = _CORE_RE.search(output)
    if core is not None:
        major, minor, patch = (int(g) for g in core.groups())
        return Engine("dbt Core", (major, minor, patch), path)
    return None


def _version_output(path: str) -> str | None:
    """Return `<path> --version` stdout, or None if it fails or hangs."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def detect_engine() -> Engine | None:
    """Find a dbt engine on PATH, preferring `dbtf` over `dbt`."""
    for name in ("dbtf", "dbt"):
        path = shutil.which(name)
        if path is None:
            continue
        output = _version_output(path)
        if output is None:
            continue
        engine = parse_engine_version(output, path)
        if engine is not None:
            return engine
    return None


def run_doctor() -> int:
    """
    Report environment diagnostics and return an exit code.

    Exit codes:
        0 - all checks passed (a supported dbt engine AND metricflow importable)
        1 - no supported dbt engine (blocking; user must install Fusion or dbt Core)
        2 - metricflow package not installed (dbt-s-layer install is broken)

    When multiple checks fail, the higher-severity code wins (2 > 1 > 0).
    """
    exit_code = 0

    engine = detect_engine()
    if engine is None:
        print("ERROR: no dbt engine found on PATH (looked for `dbtf` and `dbt`).", file=sys.stdout)
        print(f"Install dbt Fusion: {FUSION_INSTALL_URL}", file=sys.stdout)
        print(f"Or dbt Core >= 1.7: {DBT_CORE_INSTALL_URL}", file=sys.stdout)
        exit_code = max(exit_code, 1)
    elif engine.kind == "dbt Core" and engine.version < MIN_DBT_CORE_VERSION:
        print(
            f"ERROR: {engine.label} at {engine.path} is too old; "
            "dbt-s-layer needs dbt Core >= 1.7 or dbt Fusion.",
            file=sys.stdout,
        )
        exit_code = max(exit_code, 1)
    else:
        print(f"OK  dbt engine: {engine.label} at {engine.path}", file=sys.stdout)

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
