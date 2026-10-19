"""
Environment diagnostics for dbt-s-layer.

Phase 0 scope (per CONTEXT.md, revised at the Phase 0 close-out):
- Detects a dbt engine that can write `semantic_manifest.json`: dbt Fusion
  (as `dbtf`, or as `dbt` 2.x, which is how the Fusion installer names it) or
  dbt Core >= 1.7. dbt Core 1.12 parses both the legacy and the new YAML spec;
  Fusion only parses the new one.
- Reports installed `metricflow` version via `importlib.metadata.version`

With `--project-dir` (Phase 1): checks the project's parse artifacts exist, load,
and are newer than its semantic YAML (CLI-05), and warns about semantic models
the engine left out.

With `--project-dir` or `--output-dir` (Phase 3): checks the output directory
(default `models/semantic` under the project) can be written, along with the
parent directory `generate` stages files in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

FUSION_INSTALL_URL = "https://docs.getdbt.com/docs/fusion/install-fusion"
DBT_CORE_INSTALL_URL = "https://docs.getdbt.com/docs/core/installation-overview"

# dbt Core gained semantic_manifest.json in 1.7 (semantic models GA).
MIN_DBT_CORE_VERSION = (1, 7, 0)
_VERSION_TIMEOUT_SECONDS = 30

# Stable and dev Fusion builds print `dbt 2.0.8`; preview builds print
# `dbt-fusion 2.0.0-preview.212`.
_FUSION_RE = re.compile(r"^dbt(?:-fusion)? (\d+)\.(\d+)\.(\d+)", re.MULTILINE)
_CORE_RE = re.compile(r"^Core:\s*\n\s*-\s*installed:\s*(\d+)\.(\d+)\.(\d+)", re.MULTILINE)
# dbt Core before 1.0 printed `installed version: 0.21.0`.
_CORE_PRE_1_0_RE = re.compile(r"^installed version:\s*(\d+)\.(\d+)\.(\d+)", re.MULTILINE)

EngineKind = Literal["Fusion", "dbt Core"]


class VersionProbeError(Exception):
    """`<binary> --version` could not be run or did not succeed."""


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

    @property
    def supported(self) -> bool:
        """Whether this engine can write a usable semantic_manifest.json."""
        return self.kind == "Fusion" or self.version >= MIN_DBT_CORE_VERSION


@dataclass(frozen=True)
class Rejected:
    """A `dbtf`/`dbt` on PATH that is not a usable engine, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class Detection:
    """Everything `detect_engines` found on PATH."""

    engines: tuple[Engine, ...]
    rejected: tuple[Rejected, ...]


def _version_tuple(match: re.Match[str]) -> tuple[int, int, int]:
    major, minor, patch = (int(g) for g in match.groups())
    return (major, minor, patch)


def parse_engine_version(output: str, path: str) -> Engine | None:
    """Classify `<binary> --version` output as Fusion or dbt Core, or None."""
    fusion = _FUSION_RE.search(output)
    if fusion is not None and int(fusion.group(1)) >= 2:
        return Engine("Fusion", _version_tuple(fusion), path)
    for pattern in (_CORE_RE, _CORE_PRE_1_0_RE):
        core = pattern.search(output)
        if core is not None:
            return Engine("dbt Core", _version_tuple(core), path)
    return None


def _version_output(path: str) -> str:
    """Return `<path> --version` stdout, raising VersionProbeError on any failure."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VersionProbeError(f"`--version` timed out after {_VERSION_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise VersionProbeError(f"could not run it ({exc})") from exc
    if result.returncode != 0:
        raise VersionProbeError(f"`--version` exited with code {result.returncode}")
    return result.stdout


def detect_engines() -> Detection:
    """Find every dbt engine on PATH, checking `dbtf` then `dbt`."""
    engines: list[Engine] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for name in ("dbtf", "dbt"):
        path = shutil.which(name)
        if path is None:
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            output = _version_output(path)
        except VersionProbeError as exc:
            rejected.append(Rejected(path, str(exc)))
            continue
        engine = parse_engine_version(output, path)
        if engine is None:
            first_line = output.strip().splitlines()[0] if output.strip() else "(no output)"
            rejected.append(
                Rejected(path, f"it is not dbt Fusion or dbt Core (`--version`: {first_line})")
            )
        else:
            engines.append(engine)
    return Detection(tuple(engines), tuple(rejected))


def _check_project(project_dir: Path) -> int:
    """Print the artifact check for `project_dir`; return 1 if it failed, else 0."""
    from dbt_s_layer.errors import DbtSLayerError
    from dbt_s_layer.staleness import check_artifacts

    try:
        result = check_artifacts(project_dir)
    except DbtSLayerError as exc:
        print(f"ERROR artifacts: {exc}", file=sys.stdout)
        return 1
    print(f"OK  artifacts: fresh ({result.project.producer})", file=sys.stdout)
    for warning in result.warnings:
        print(f"WARN {warning}", file=sys.stdout)
    manifest = result.project.dbt_manifest
    if manifest.metadata.adapter_type == "snowflake":
        from dbt_s_layer import snowflake

        if snowflake.MATERIALIZATION_MACRO in manifest.macros:
            print(f"OK  {snowflake.PACKAGE}: installed", file=sys.stdout)
        else:
            print(
                f"WARN {snowflake.PACKAGE}: not installed, so `dbt run` fails on the generated "
                f"sem__ models. Add Snowflake-Labs/{snowflake.PACKAGE} to packages.yml and run "
                "`dbt deps`.",
                file=sys.stdout,
            )
    if manifest.metadata.adapter_type == "databricks":
        from dbt_s_layer import databricks

        if databricks.MATERIALIZATION_MACRO in manifest.macros:
            print("OK  dbt-databricks metric views: installed", file=sys.stdout)
        else:
            print(
                "WARN dbt-databricks metric views: no metric_view materialization, so `dbt run` "
                "fails on the generated sem__ models. Install dbt-databricks >= 1.12.0.",
                file=sys.stdout,
            )
    return 0


def _check_output_dir(output_dir: Path) -> int:
    """Print whether `generate` could write `output_dir`; return 1 if not, else 0."""
    if output_dir.exists() and not output_dir.is_dir():
        print(f"ERROR output directory: {output_dir} is not a directory", file=sys.stdout)
        return 1
    # `generate` stages files beside the output directory, then moves them in.
    needed = [output_dir, output_dir.parent] if output_dir.exists() else [output_dir.parent]
    existing = [p for p in needed if p.exists()]
    if not existing:
        ancestor = output_dir.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        existing = [ancestor]
    for path in existing:
        if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
            print(
                f"ERROR output directory: {path} is not writable, so `generate` cannot "
                f"write {output_dir}",
                file=sys.stdout,
            )
            return 1
    if output_dir.exists():
        print(f"OK  output directory: {output_dir} is writable", file=sys.stdout)
    else:
        print(
            f"OK  output directory: {output_dir} does not exist yet; `generate` will "
            f"create it in {existing[0]}",
            file=sys.stdout,
        )
    return 0


def run_doctor(project_dir: Path | None = None, output_dir: Path | None = None) -> int:
    """
    Report environment diagnostics and return an exit code.

    With `project_dir`, also check that project's parse artifacts. With
    `project_dir` or `output_dir`, also check the output directory (relative to
    the project, or to the working directory without one).

    Exit codes:
        0 - all checks passed (a supported dbt engine AND metricflow importable,
            and, with `project_dir`, fresh artifacts; warnings do not fail)
        1 - no supported dbt engine (blocking; user must install Fusion or dbt Core),
            or the project's artifacts are missing, stale or unreadable, or the
            output directory cannot be written
        2 - metricflow package not installed (dbt-s-layer install is broken)

    When multiple checks fail, the higher-severity code wins (2 > 1 > 0).
    """
    exit_code = 0

    found = detect_engines()
    for engine in found.engines:
        if engine.supported:
            print(f"OK  dbt engine: {engine.label} at {engine.path}", file=sys.stdout)
        else:
            print(
                f"WARN dbt engine: {engine.label} at {engine.path} is too old "
                "(dbt-s-layer needs dbt Core >= 1.7 or dbt Fusion)",
                file=sys.stdout,
            )
    for rejected in found.rejected:
        print(f"WARN found {rejected.path} but {rejected.reason}", file=sys.stdout)

    supported = [e for e in found.engines if e.supported]
    if not supported:
        print(
            "ERROR: no supported dbt engine found on PATH (looked for `dbtf` and `dbt`).",
            file=sys.stdout,
        )
        print(f"Install dbt Fusion: {FUSION_INSTALL_URL}", file=sys.stdout)
        print(f"Or dbt Core >= 1.7: {DBT_CORE_INSTALL_URL}", file=sys.stdout)
        exit_code = max(exit_code, 1)
    elif all(e.kind == "Fusion" for e in supported):
        print(
            "NOTE Fusion only parses the new YAML spec. Legacy-spec projects "
            f"(`semantic_models:` with `measures:`) need dbt Core >= 1.7: {DBT_CORE_INSTALL_URL}",
            file=sys.stdout,
        )

    metricflow_ok = True
    try:
        mf_version = version("metricflow")
        print(f"OK  metricflow version: {mf_version}", file=sys.stdout)
    except PackageNotFoundError:
        metricflow_ok = False
        print(
            "ERROR: metricflow is not installed (dbt-s-layer install is broken).",
            file=sys.stdout,
        )
        exit_code = max(exit_code, 2)

    if project_dir is not None:
        if metricflow_ok:
            exit_code = max(exit_code, _check_project(project_dir))
        else:
            # Loading the artifacts needs metricflow.
            print(
                "SKIP artifacts: not checked because metricflow is not installed", file=sys.stdout
            )

    if project_dir is not None or output_dir is not None:
        from dbt_s_layer._paths import DEFAULT_OUTPUT_DIR, resolve_output_dir

        base = project_dir if project_dir is not None else Path.cwd()
        resolved = resolve_output_dir(base.resolve(), output_dir or DEFAULT_OUTPUT_DIR)
        exit_code = max(exit_code, _check_output_dir(resolved))

    return exit_code
