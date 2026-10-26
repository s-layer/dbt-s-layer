"""
Read a dbt project's own YAML: dbt_project.yml, its semantic YAML, its local packages.

Kept apart from `artifacts` so the pre-commit hook can read it before metricflow
is imported (the first `dbt parse` runs while it is).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dbt_s_layer._yaml import YamlReadError, read_yaml
from dbt_s_layer.errors import ProjectNotFoundError

# The files that list a project's packages, `local:` ones among them.
DEPENDENCY_FILES = ("packages.yml", "dependencies.yml")
DEFAULT_PACKAGES_INSTALL_PATH = "dbt_packages"


def read_dbt_project(project_dir: Path) -> dict[str, Any]:
    """Return the parsed dbt_project.yml of `project_dir`."""
    path = project_dir / "dbt_project.yml"
    if not path.is_file():
        raise ProjectNotFoundError(f"{project_dir} is not a dbt project: {path} does not exist.")
    try:
        data = read_yaml(path)
    except YamlReadError as exc:
        raise ProjectNotFoundError(f"{path} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectNotFoundError(f"{path} could not be read: it is not a YAML mapping.")
    return {str(k): v for k, v in cast("dict[object, object]", data).items()}


# dbt parses property YAML (semantic_models:, metrics:, saved_queries:) under all
# of these, with these defaults.
RESOURCE_PATHS = {
    "model-paths": ("models",),
    "seed-paths": ("seeds",),
    "snapshot-paths": ("snapshots",),
    "analysis-paths": ("analyses",),
    "macro-paths": ("macros",),
}
_SEMANTIC_TOP_LEVEL_KEYS = ("semantic_models", "metrics", "saved_queries")


@dataclass(frozen=True)
class DeclaredSemanticModel:
    """A semantic model declared in YAML, and where."""

    name: str  # lowercased, as metricflow names it
    file: Path
    # Declared under top-level `semantic_models:` (legacy spec).
    legacy: bool
    # Attached to a versioned model (the entry has `versions:`).
    versioned: bool
    # Lowercased names of the metrics declared on the same `models:` entry
    # (new spec), which go wherever the semantic model goes.
    metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticYamlScan:
    """The semantic YAML files under a project's model paths."""

    files: tuple[Path, ...]
    newest_mtime: float | None
    newest_file: Path | None
    declared_semantic_models: tuple[DeclaredSemanticModel, ...]
    # Lowercased names of the saved queries the YAML declares.
    declared_saved_queries: tuple[str, ...]
    # Files that could not be read; their declarations are unknown.
    warnings: tuple[str, ...]


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return str(path)


def _resource_paths(project_dir: Path) -> list[str]:
    config = read_dbt_project(project_dir)
    paths: list[str] = []
    for key, default in RESOURCE_PATHS.items():
        value = config.get(key)
        if isinstance(value, list):
            paths.extend(str(p) for p in cast("list[object]", value))
        else:
            paths.extend(default)
    return paths


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast("dict[str, object]", item)
        for item in cast("list[object]", value)
        if isinstance(item, dict)
    ]


def _declarations(data: dict[str, object], file: Path) -> tuple[bool, list[DeclaredSemanticModel]]:
    """Return whether the file is semantic YAML, and the enabled semantic models it declares."""
    counts = any(key in data for key in _SEMANTIC_TOP_LEVEL_KEYS)
    declared: list[DeclaredSemanticModel] = []

    for entry in _mappings(data.get("models")):
        if "semantic_model" not in entry:
            continue
        counts = True
        config = entry["semantic_model"]
        name = entry.get("name")
        model_config = entry.get("config")
        if (
            isinstance(model_config, dict)
            and cast("dict[str, object]", model_config).get("enabled") is False
        ):
            # A disabled model takes its semantic model with it.
            continue
        if isinstance(config, dict):
            config = cast("dict[str, object]", config)
            enabled = config.get("enabled", True) is not False
            name = config.get("name") or name
        else:
            enabled = config is not False and config is not None
        if enabled and isinstance(name, str):
            metrics = (m.get("name") for m in _mappings(entry.get("metrics")))
            declared.append(
                DeclaredSemanticModel(
                    name.lower(),
                    file,
                    legacy=False,
                    versioned="versions" in entry,
                    metrics=tuple(m.lower() for m in metrics if isinstance(m, str)),
                )
            )

    for entry in _mappings(data.get("semantic_models")):
        name = entry.get("name")
        config = entry.get("config")
        disabled = (
            isinstance(config, dict) and cast("dict[str, object]", config).get("enabled") is False
        )
        if isinstance(name, str) and not disabled:
            declared.append(DeclaredSemanticModel(name.lower(), file, legacy=True, versioned=False))

    return counts, declared


def _saved_query_names(data: dict[str, object]) -> list[str]:
    names = (entry.get("name") for entry in _mappings(data.get("saved_queries")))
    return [name.lower() for name in names if isinstance(name, str)]


def scan_semantic_yaml(project_dir: Path) -> SemanticYamlScan:
    """
    Find the YAML that feeds semantic_manifest.json.

    Looks under every resource path dbt reads property YAML from (`model-paths`,
    `seed-paths`, `snapshot-paths`, `analysis-paths`, `macro-paths`).
    """
    try:
        return _scan(project_dir)
    except OSError as exc:
        raise ProjectNotFoundError(f"{project_dir} could not be scanned: {exc}") from exc


def _candidate_files(project_dir: Path) -> set[Path]:
    candidates: set[Path] = set()
    for resource_path in _resource_paths(project_dir):
        root = project_dir / resource_path
        if root.is_dir():
            candidates.update(p for p in root.rglob("*") if p.suffix in (".yml", ".yaml"))
    return candidates


def _scan(project_dir: Path) -> SemanticYamlScan:
    candidates = _candidate_files(project_dir)

    files: list[Path] = []
    declared: list[DeclaredSemanticModel] = []
    saved_queries: list[str] = []
    warnings: list[str] = []
    for file in sorted(candidates):
        if not file.is_file():
            continue
        try:
            data = read_yaml(file)
        except YamlReadError as exc:
            warnings.append(
                f"{_rel(file, project_dir)} could not be read ({exc}), so it was not checked "
                "for staleness; `dbt parse` will report it."
            )
            continue
        if not isinstance(data, dict):
            continue
        mapping = cast("dict[str, object]", data)
        counts, file_declared = _declarations(mapping, file)
        if counts:
            files.append(file)
            declared.extend(file_declared)
            saved_queries.extend(_saved_query_names(mapping))

    mtimes = {file: file.stat().st_mtime for file in files}
    newest_file = max(mtimes, key=lambda f: mtimes[f]) if mtimes else None
    return SemanticYamlScan(
        files=tuple(files),
        newest_mtime=mtimes[newest_file] if newest_file is not None else None,
        newest_file=newest_file,
        declared_semantic_models=tuple(declared),
        declared_saved_queries=tuple(saved_queries),
        warnings=tuple(warnings),
    )


def local_packages(project_dir: Path) -> tuple[Path, ...]:
    """The directories packages.yml and dependencies.yml install as `local:` packages."""
    found: list[Path] = []
    for name in DEPENDENCY_FILES:
        path = project_dir / name
        if not path.is_file():
            continue
        try:
            data = read_yaml(path)
        except YamlReadError:
            continue
        if not isinstance(data, dict):
            continue
        for entry in _mappings(cast("dict[str, object]", data).get("packages")):
            local = entry.get("local")
            if isinstance(local, str) and local and "{{" not in local:
                found.append(Path(os.path.normpath(project_dir / local)))
    return tuple(found)


def _installed_packages(project_dir: Path) -> list[Path]:
    """The packages `dbt deps` installed into the project's packages-install-path."""
    setting = read_dbt_project(project_dir).get("packages-install-path")
    if not isinstance(setting, str) or not setting or "{{" in setting:
        setting = DEFAULT_PACKAGES_INSTALL_PATH
    root = project_dir / setting
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if (d / "dbt_project.yml").is_file())


def _legacy_file(project_dir: Path) -> Path | None:
    for file in sorted(_candidate_files(project_dir)):
        try:
            # Cheap test first: only files naming `semantic_models` can declare one.
            if b"semantic_models" not in file.read_bytes():
                continue
            data = read_yaml(file)
        except (OSError, YamlReadError):
            continue
        if isinstance(data, dict):
            _, declared = _declarations(cast("dict[str, object]", data), file)
            if any(d.legacy for d in declared):
                return file
    return None


def legacy_declaration(project_dir: Path) -> Path | None:
    """
    A file declaring a legacy-spec semantic model (top-level `semantic_models:`), or None.

    Looks in the project, then in its local packages (from packages.yml or
    dependencies.yml, installed or not) and the packages `dbt deps` installed:
    their semantic models reach the project's semantic_manifest.json too. A
    directory that cannot be read is skipped; `dbt parse` reports it.
    """
    seen: set[Path] = set()
    queue = [project_dir]
    while queue:
        directory = Path(os.path.normpath(queue.pop(0)))
        real = directory.resolve()
        if real in seen:
            continue
        seen.add(real)
        try:
            found = _legacy_file(directory)
            if found is not None:
                return found
            queue += [*local_packages(directory), *_installed_packages(directory)]
        except (ProjectNotFoundError, OSError):
            continue
    return None
