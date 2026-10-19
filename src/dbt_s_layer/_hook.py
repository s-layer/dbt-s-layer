"""
`dbt-s-layer pre-commit`: what the pre-commit hook runs (HOOK-01).

pre-commit filters files by name only, so the hook gets every staged YAML file
and decides itself, from the text, whether any holds semantic YAML. Only then
does it import the generator (and metricflow). For each dbt project with such a
file it runs `dbt parse`, since the user has just edited the YAML the artifacts
were parsed from, then `generate`. It exits 1 when it changed anything, because
pre-commit only notices changes to tracked files (05-RESEARCH finding 5).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_s_layer._paths import resolve_output_dir

if TYPE_CHECKING:
    from collections.abc import Sequence

PROJECT_FILE = "dbt_project.yml"
_YAML_SUFFIXES = (".yml", ".yaml")
# A line whose key (after indentation and an optional list dash) is one that feeds
# semantic_manifest.json. `semantic_model:` is the new spec's key on a model.
_SEMANTIC_KEY_RE = re.compile(
    rb"^[ \t]*(?:-[ \t]+)?(?:semantic_models?|metrics|saved_queries)[ \t]*:", re.MULTILINE
)
_PROFILE_KEY_RE = re.compile(rb"^profile[ \t]*:", re.MULTILINE)
_PARSE_OUTPUT_LINES = 30


@dataclass(frozen=True)
class HookOptions:
    """The `pre-commit` command's options."""

    output_dir: Path
    project_dir: Path | None = None
    target: str | None = None
    adapter: str | None = None
    profiles_dir: Path | None = None
    # Executable that parses; None picks `dbtf`, then `dbt`, from PATH.
    dbt: str | None = None
    parse: bool = True


def _out(message: str) -> None:
    print(message, file=sys.stdout)


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def is_semantic_yaml(path: Path) -> bool:
    """Whether `path` is a YAML file with a `semantic_models:`, `metrics:` or similar key."""
    if path.suffix not in _YAML_SUFFIXES:
        return False
    try:
        text = path.read_bytes()
    except OSError:
        return False
    return _SEMANTIC_KEY_RE.search(text) is not None


def _absolute(path: Path, cwd: Path) -> Path:
    return Path(os.path.normpath(cwd / path))


def _names_profile(project_file: Path) -> bool:
    try:
        return _PROFILE_KEY_RE.search(project_file.read_bytes()) is not None
    except OSError:
        return False


def _nearest_project(path: Path, stop: Path) -> Path | None:
    """
    The dbt project above `path`, not looking above `stop`.

    That is the closest directory with a dbt_project.yml that names a `profile:`. A
    dbt_project.yml without one is a package (a local package, say), which cannot be
    parsed on its own and whose semantic models reach the project that installs it.
    With no such directory, the closest dbt_project.yml.
    """
    nearest: Path | None = None
    for directory in (path.parent, *path.parent.parents):
        project_file = directory / PROJECT_FILE
        if project_file.is_file():
            if _names_profile(project_file):
                return directory
            nearest = nearest or directory
        if directory == stop:
            break
    return nearest


def _triggers(path: Path, project: Path, output_dir: Path) -> bool:
    if path == project / PROJECT_FILE:
        return True
    if path.is_relative_to(resolve_output_dir(project, output_dir)):
        return True
    return is_semantic_yaml(path)


def select_projects(
    files: Sequence[Path], *, project_dir: Path | None, output_dir: Path, cwd: Path
) -> list[Path]:
    """
    The dbt projects the passed files call for regenerating, sorted.

    A file counts when it is semantic YAML, its project's dbt_project.yml, or inside
    its project's output directory. Its project is `project_dir` when given (files
    outside it are ignored), else the nearest one above it. With no files at all,
    `project_dir` or `cwd` is the project.
    """
    pinned = _absolute(project_dir, cwd) if project_dir is not None else None
    if not files:
        return [pinned or cwd]
    selected: set[Path] = set()
    for file in files:
        path = _absolute(file, cwd)
        project = pinned if pinned is not None else _nearest_project(path, cwd)
        if project is None or project in selected or not path.is_relative_to(project):
            continue
        if _triggers(path, project, output_dir):
            selected.add(project)
    return sorted(selected)


def _display(path: Path, cwd: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return str(path)


def _find_dbt(option: str | None, cwd: Path) -> str | None:
    if option is None:
        return shutil.which("dbtf") or shutil.which("dbt")
    if os.sep in option or "/" in option:
        path = _absolute(Path(option), cwd)
        return str(path) if path.is_file() else None
    return shutil.which(option)


@dataclass(frozen=True)
class _Parse:
    """A `dbt parse` running in one project."""

    shown: str
    process: subprocess.Popen[str] | None


def _start_parse(dbt: str, project: Path, options: HookOptions, cwd: Path) -> _Parse:
    """Start `<dbt> parse` in `project`; on failure to start, print why."""
    command = [dbt, "parse"]
    if options.target is not None:
        command += ["--target", options.target]
    if options.profiles_dir is not None:
        command += ["--profiles-dir", str(_absolute(options.profiles_dir, cwd))]
    shown = f"`{Path(dbt).name} parse` in {_display(project, cwd)}"
    try:
        process = subprocess.Popen(
            command, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    except OSError as exc:
        _err(f"ERROR: could not run {shown}: {exc}")
        return _Parse(shown, None)
    return _Parse(shown, process)


def _finish_parse(parse: _Parse) -> bool:
    """Wait for a parse; on failure print the end of its output and return False."""
    if parse.process is None:
        return False
    output, _ = parse.process.communicate()
    if parse.process.returncode == 0:
        return True
    _err(
        f"ERROR: {parse.shown} failed (exit code {parse.process.returncode}). "
        "Last lines of its output:"
    )
    for line in output.strip().splitlines()[-_PARSE_OUTPUT_LINES:]:
        _err(f"  {line}")
    _err("Fix the project, or pass --no-parse if something else runs `dbt parse` first.")
    return False


def run_hook(files: Sequence[Path], options: HookOptions) -> int:
    """Parse and regenerate every project the files call for; 1 if anything changed or failed."""
    cwd = Path.cwd()
    projects = select_projects(
        files, project_dir=options.project_dir, output_dir=options.output_dir, cwd=cwd
    )
    if not projects:
        return 0
    dbt: str | None = None
    if options.parse:
        dbt = _find_dbt(options.dbt, cwd)
        if dbt is None:
            looked = f"`{options.dbt}`" if options.dbt else "`dbtf` and `dbt` on PATH"
            _err(
                f"ERROR: no dbt executable found (looked for {looked}). Install dbt Fusion or "
                "dbt Core, pass --dbt with the executable that parses the project, or pass "
                "--no-parse if something else runs `dbt parse` first."
            )
            return 1

    # The first parse runs while the generator is imported (about a second each).
    first = _start_parse(dbt, projects[0], options, cwd) if dbt is not None else None
    # Imported here: it loads metricflow, which a commit without semantic YAML never needs.
    from dbt_s_layer import _commands

    profiles_dir = _absolute(options.profiles_dir, cwd) if options.profiles_dir else None
    failed = False
    updated: list[str] = []
    for index, project in enumerate(projects):
        if dbt is not None:
            parse = first if index == 0 and first else _start_parse(dbt, project, options, cwd)
            if not _finish_parse(parse):
                failed = True
                continue
        result = _commands.generate_project(
            _commands.Options(
                project_dir=project,
                output_dir=options.output_dir,
                target=options.target,
                adapter=options.adapter,
                profiles_dir=profiles_dir,
            )
        )
        if result is None:
            failed = True
            continue
        output_dir, diff = result
        for label, names in (
            ("new", diff.missing),
            ("changed", diff.changed),
            ("removed", diff.stale),
        ):
            updated.extend(f"  {label}: {_display(output_dir / name, cwd)}" for name in names)
    if updated:
        _out("dbt-s-layer updated generated files:")
        for line in updated:
            _out(line)
        _out("Stage them (git add) and commit again.")
    return 1 if failed or updated else 0
