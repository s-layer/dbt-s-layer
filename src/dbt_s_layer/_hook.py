"""
`dbt-s-layer pre-commit`: what the pre-commit hook runs (HOOK-01).

pre-commit filters files by name only, so the hook gets every staged YAML file
and decides itself, from the text, whether any holds semantic YAML. Only then
does it import the generator (and metricflow). For each dbt project with such a
file it runs `dbt parse`, since the user has just edited the YAML the artifacts
were parsed from, then `generate`. It exits 1 when it changed anything, because
pre-commit only notices changes to tracked files (05-RESEARCH finding 5).

The engine follows the project's YAML spec: Fusion ignores legacy-spec
`semantic_models:`, so a project (or a package it installs) that declares one is
parsed with dbt Core. `dbt parse` and `generate` are given the same profiles.yml.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_s_layer._doctor import Detection, detect_engines
from dbt_s_layer._paths import resolve_output_dir
from dbt_s_layer._project_yaml import legacy_declaration, local_packages
from dbt_s_layer.errors import ProfileError
from dbt_s_layer.profiles import find_profiles

if TYPE_CHECKING:
    from collections.abc import Sequence

PROJECT_FILE = "dbt_project.yml"
_YAML_SUFFIXES = (".yml", ".yaml")
# A line whose key (after indentation and an optional list dash) is one that feeds
# semantic_manifest.json. `semantic_model:` is the new spec's key on a model, and
# `time_spine:` marks the model metricflow takes its time spine from.
_SEMANTIC_KEY_RE = re.compile(
    rb"^[ \t]*(?:-[ \t]+)?(?:semantic_models?|metrics|saved_queries|time_spine)[ \t]*:",
    re.MULTILINE,
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
    # Executable that parses; None picks one from PATH by the project's YAML spec.
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


def _nearest_project(path: Path, stop: Path, packages: dict[Path, tuple[Path, ...]]) -> Path | None:
    """
    The dbt project `path` belongs to, not looking above `stop`.

    Start from the closest directory with a dbt_project.yml. While a project further
    up lists it as a `local:` package (packages.yml or dependencies.yml), that
    project is the one parsed: a package's semantic models reach the
    semantic_manifest.json of the project that installs it, whether or not its own
    dbt_project.yml names a `profile:`. A package nothing here lists, and that
    names no `profile:`, cannot be parsed on its own, so the closest project above
    it that names one is used. `packages` caches each project's local packages.
    """
    chain: list[Path] = []
    for directory in (path.parent, *path.parent.parents):
        if (directory / PROJECT_FILE).is_file():
            chain.append(directory)
        if directory == stop:
            break
    if not chain:
        return None
    project = chain[0]
    for outer in chain[1:]:
        if outer not in packages:
            packages[outer] = local_packages(outer)
        if project in packages[outer]:
            project = outer
    if _names_profile(project / PROJECT_FILE):
        return project
    above = chain[chain.index(project) + 1 :]
    return next((d for d in above if _names_profile(d / PROJECT_FILE)), project)


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
    outside it are ignored), else the one `_nearest_project` finds. With no files at all,
    `project_dir` or `cwd` is the project.
    """
    pinned = _absolute(project_dir, cwd) if project_dir is not None else None
    if not files:
        return [pinned or cwd]
    selected: set[Path] = set()
    packages: dict[Path, tuple[Path, ...]] = {}
    for file in files:
        path = _absolute(file, cwd)
        project = pinned if pinned is not None else _nearest_project(path, cwd, packages)
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


def _find_dbt(option: str, cwd: Path) -> str | None:
    if os.sep in option or "/" in option:
        path = _absolute(Path(option), cwd)
        return str(path) if path.is_file() else None
    return shutil.which(option)


def _choose_dbt(project: Path, options: HookOptions, cwd: Path) -> str | None:
    """
    The executable that parses `project`; on failure print why and return None.

    `--dbt` wins. Otherwise a project that declares a legacy-spec semantic model
    (itself or in a package it installs) needs dbt Core, since Fusion ignores
    those; any other project uses `dbtf`, then `dbt`, from PATH.
    """
    if options.dbt is not None:
        dbt = _find_dbt(options.dbt, cwd)
        if dbt is None:
            _err(
                f"ERROR: no dbt executable found (looked for `{options.dbt}`). Pass --dbt "
                "with the executable that parses the project, or pass --no-parse if "
                "something else runs `dbt parse` first."
            )
        return dbt
    legacy = legacy_declaration(project)
    if legacy is None:
        dbt = shutil.which("dbtf") or shutil.which("dbt")
        if dbt is None:
            _err(
                "ERROR: no dbt executable found (looked for `dbtf` and `dbt` on PATH). "
                "Install dbt Fusion or dbt Core, pass --dbt with the executable that parses "
                "the project, or pass --no-parse if something else runs `dbt parse` first."
            )
        return dbt
    found = _engines()
    for engine in found.engines:
        if engine.kind == "dbt Core" and engine.supported:
            return engine.path
    seen = ", ".join(f"{e.label} at {e.path}" for e in found.engines) or "no dbt engine"
    _err(
        f"ERROR: {_display(legacy, cwd)} declares a legacy-spec semantic model "
        "(top-level `semantic_models:`), which Fusion ignores, so "
        f"{_display(project, cwd)} must be parsed with dbt Core >= 1.7; found {seen} on "
        "PATH. Install dbt Core, pass --dbt with a dbt Core executable, move the semantic "
        "models to the new YAML spec, or pass --no-parse if something else runs "
        "`dbt parse` first."
    )
    return None


def _profiles_dir(project: Path, options: HookOptions, cwd: Path) -> Path | None:
    """
    The directory of the profiles.yml `generate` reads for `project`, or None.

    Found once, in the order `profiles.find_profiles` looks, and passed to `dbt
    parse` too: it runs in the project, so it would otherwise look there before
    the working directory.
    """
    if options.profiles_dir is not None:
        return _absolute(options.profiles_dir, cwd)
    try:
        return _absolute(find_profiles(project).parent, cwd)
    except ProfileError:
        return None


@dataclass(frozen=True)
class _Parse:
    """A `dbt parse` running in one project."""

    shown: str
    process: subprocess.Popen[str] | None


def _start_parse(
    dbt: str, project: Path, options: HookOptions, profiles_dir: Path | None, cwd: Path
) -> _Parse:
    """Start `<dbt> parse` in `project`; on failure to start, print why."""
    command = [dbt, "parse"]
    if options.target is not None:
        command += ["--target", options.target]
    if profiles_dir is not None:
        command += ["--profiles-dir", str(profiles_dir)]
    shown = f"`{Path(dbt).name} parse` in {_display(project, cwd)}"
    try:
        process = subprocess.Popen(
            command, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    except OSError as exc:
        _err(f"ERROR: could not run {shown}: {exc}")
        return _Parse(shown, None)
    return _Parse(shown, process)


@dataclass(frozen=True)
class _Prepared:
    """What one project's run needs; `parse` is None with --no-parse or on failure."""

    project: Path
    profiles_dir: Path | None
    parse: _Parse | None
    ok: bool


def _prepare(project: Path, options: HookOptions, cwd: Path) -> _Prepared:
    """Choose the engine and profiles.yml for `project` and start its parse."""
    profiles_dir = _profiles_dir(project, options, cwd)
    if not options.parse:
        return _Prepared(project, profiles_dir, None, ok=True)
    dbt = _choose_dbt(project, options, cwd)
    if dbt is None:
        return _Prepared(project, profiles_dir, None, ok=False)
    parse = _start_parse(dbt, project, options, profiles_dir, cwd)
    return _Prepared(project, profiles_dir, parse, ok=True)


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


@functools.cache
def _engines() -> Detection:
    # `dbt --version` takes seconds with dbt Core, so probe once per run.
    return detect_engines()


def run_hook(files: Sequence[Path], options: HookOptions) -> int:
    """Parse and regenerate every project the files call for; 1 if anything changed or failed."""
    _engines.cache_clear()
    cwd = Path.cwd()
    projects = select_projects(
        files, project_dir=options.project_dir, output_dir=options.output_dir, cwd=cwd
    )
    if not projects:
        return 0

    # The first parse runs while the generator is imported (about a second each).
    first = _prepare(projects[0], options, cwd)
    # Imported here: it loads metricflow, which a commit without semantic YAML never needs.
    from dbt_s_layer import _commands

    failed = False
    updated: list[str] = []
    for index, project in enumerate(projects):
        prepared = first if index == 0 else _prepare(project, options, cwd)
        if not prepared.ok or (prepared.parse is not None and not _finish_parse(prepared.parse)):
            failed = True
            continue
        result = _commands.generate_project(
            _commands.Options(
                project_dir=project,
                output_dir=options.output_dir,
                target=options.target,
                adapter=options.adapter,
                profiles_dir=prepared.profiles_dir,
                # A semantic model the engine dropped (wrong spec) must not lose its files.
                missing_is_error=True,
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
