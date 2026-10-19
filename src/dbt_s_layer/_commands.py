"""
The `generate` and `check` commands (CLI-01, CLI-02, CLI-04).

Both load the project through `check_artifacts` (CLI-05), pick the SQL dialect,
render every generated file in memory, and then either write it (`generate`) or
compare it with the output directory (`check`).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_s_layer._paths import resolve_output_dir
from dbt_s_layer.errors import DbtSLayerError
from dbt_s_layer.output import apply_output, diff_output, project_model_paths, render_output
from dbt_s_layer.profiles import adapter_for_target
from dbt_s_layer.staleness import check_artifacts

if TYPE_CHECKING:
    from pathlib import Path

    from dbt_s_layer.output import OutputDiff


@dataclass(frozen=True)
class Options:
    """What `generate` and `check` share."""

    project_dir: Path
    output_dir: Path
    target: str | None = None
    adapter: str | None = None
    profiles_dir: Path | None = None


def _out(message: str) -> None:
    print(message, file=sys.stdout)


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _render(options: Options) -> tuple[dict[str, str], Path]:
    checked = check_artifacts(options.project_dir)
    for warning in checked.warnings:
        _err(f"WARN {warning}")
    project = checked.project
    output_dir = resolve_output_dir(project.project_dir, options.output_dir)
    model_paths = project_model_paths(project.project_dir)
    if not any(output_dir.is_relative_to(p) for p in model_paths):
        shown = ", ".join(str(p) for p in model_paths)
        _err(
            f"WARN output directory {output_dir} is not under the project's model-paths "
            f"({shown}), so dbt will not build the generated models."
        )
    adapter = options.adapter
    if adapter is None and options.target is not None:
        adapter = adapter_for_target(
            project.project_dir, options.target, profiles_dir=options.profiles_dir
        )
    files = render_output(project, adapter=adapter, output_dir=output_dir)
    return files, output_dir


def _count(n: int) -> str:
    return f"{n} file{'' if n == 1 else 's'}"


def _summary(diff: OutputDiff) -> str:
    parts = [
        f"{len(names)} {label}"
        for names, label in (
            (diff.missing, "new"),
            (diff.changed, "changed"),
            (diff.stale, "removed"),
        )
        if names
    ]
    return ", ".join(parts)


def run_generate(options: Options) -> int:
    """Write the generated files; return the exit code."""
    try:
        files, output_dir = _render(options)
        diff = apply_output(files, output_dir)
    except DbtSLayerError as exc:
        _err(f"ERROR: {exc}")
        return 1
    except OSError as exc:
        _err(f"ERROR: could not write to the output directory: {exc}")
        return 1
    if diff.clean:
        _out(f"{output_dir} is up to date ({_count(len(files))}).")
    else:
        _out(f"Generated {_count(len(files))} in {output_dir} ({_summary(diff)}).")
    return 0


def run_check(options: Options) -> int:
    """Compare the output directory with a fresh render; return 1 when they differ."""
    try:
        files, output_dir = _render(options)
        diff = diff_output(files, output_dir)
    except DbtSLayerError as exc:
        _err(f"ERROR: {exc}")
        return 1
    except OSError as exc:
        _err(f"ERROR: could not read the output directory: {exc}")
        return 1
    if diff.clean:
        _out(f"{output_dir} is up to date ({_count(len(files))}).")
        return 0
    _out(f"{output_dir} is out of date:")
    for label, names in (
        ("changed", diff.changed),
        ("missing", diff.missing),
        ("stale", diff.stale),
    ):
        for name in names:
            _out(f"  {label}: {name}")
    _out("Run `dbt-s-layer generate` with the same options to update it.")
    return 1
