"""
Check that a project's parse artifacts exist and match its semantic YAML (CLI-05).

The scan also collects the semantic models the YAML declares, so a semantic model
the engine silently left out of semantic_manifest.json is reported. dbt Core
1.12.6 does this for semantic models on versioned models, and Fusion for every
legacy-spec `semantic_models:` entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from dbt_s_layer._yaml import YamlReadError, read_yaml
from dbt_s_layer.artifacts import (
    SEMANTIC_MANIFEST,
    missing_artifact_error,
    read_dbt_project,
    resolve_target_dir,
)
from dbt_s_layer.errors import ProjectNotFoundError, StaleArtifactError
from dbt_s_layer.project import SemanticProject, load_project

if TYPE_CHECKING:
    from pathlib import Path

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


@dataclass(frozen=True)
class ArtifactCheck:
    """A loaded project whose artifacts are fresh, and anything worth warning about."""

    project: SemanticProject
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
        if isinstance(config, dict):
            config = cast("dict[str, object]", config)
            enabled = config.get("enabled", True) is not False
            name = config.get("name") or name
        else:
            enabled = config is not False and config is not None
        if enabled and isinstance(name, str):
            declared.append(
                DeclaredSemanticModel(
                    name.lower(), file, legacy=False, versioned="versions" in entry
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


def _scan(project_dir: Path) -> SemanticYamlScan:
    candidates: set[Path] = set()
    for resource_path in _resource_paths(project_dir):
        root = project_dir / resource_path
        if root.is_dir():
            candidates.update(p for p in root.rglob("*") if p.suffix in (".yml", ".yaml"))

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


def _missing_hint(declared: DeclaredSemanticModel, producer: str) -> str:
    if producer.startswith("dbt Core") and declared.versioned:
        return "; dbt Core drops semantic models on versioned models, so parse with Fusion"
    if producer.startswith("Fusion") and declared.legacy:
        return (
            "; Fusion ignores legacy-spec `semantic_models:`, so parse with dbt Core >= 1.7 "
            "or move it to the new YAML spec"
        )
    return ""


def _stale(semantic_manifest: Path, project_dir: Path, reason: str) -> StaleArtifactError:
    return StaleArtifactError(
        f"{semantic_manifest} {reason}. "
        f"Run `dbt parse` (dbt Core or Fusion) in {project_dir} to refresh it."
    )


def _check_removed(
    project: SemanticProject, scan: SemanticYamlScan, semantic_manifest: Path
) -> None:
    """Raise if the artifact still holds a semantic model or saved query no YAML declares."""
    root = project.dbt_manifest.metadata.project_name
    declared = {d.name for d in scan.declared_semantic_models}
    packages = {
        sm.name.lower(): sm.package_name for sm in project.dbt_manifest.semantic_models.values()
    }
    for sm in sorted(project.semantic_manifest.semantic_models, key=lambda sm: sm.name):
        name = sm.name.lower()
        if packages.get(name, root) == root and name not in declared:
            raise _stale(
                semantic_manifest,
                project.project_dir,
                f"still contains semantic model {name!r}, which no YAML in the project "
                "declares (or which is now disabled)",
            )

    declared_queries = set(scan.declared_saved_queries)
    query_packages = {
        sq.name.lower(): sq.package_name for sq in project.dbt_manifest.saved_queries.values()
    }
    for sq in sorted(project.semantic_manifest.saved_queries, key=lambda sq: sq.name):
        name = sq.name.lower()
        if query_packages.get(name, root) == root and name not in declared_queries:
            raise _stale(
                semantic_manifest,
                project.project_dir,
                f"still contains saved query {name!r}, which no YAML in the project declares",
            )


def check_artifacts(project_dir: Path) -> ArtifactCheck:
    """
    Load the project's artifacts after checking they exist and are fresh.

    Raises ArtifactNotFoundError, StaleArtifactError, or any loading error.
    Stale means semantic_manifest.json is older than dbt_project.yml or a
    semantic YAML file (an equal modification time counts as fresh), or it
    still holds a semantic model or saved query the project's YAML no longer
    declares.
    """
    project_dir = project_dir.resolve()
    semantic_manifest = resolve_target_dir(project_dir) / SEMANTIC_MANIFEST
    if not semantic_manifest.exists():
        raise missing_artifact_error(semantic_manifest, project_dir)

    scan = scan_semantic_yaml(project_dir)
    try:
        artifact_mtime = semantic_manifest.stat().st_mtime
        inputs = [project_dir / "dbt_project.yml", *scan.files]
        newer = {f: m for f in inputs if (m := f.stat().st_mtime) > artifact_mtime}
    except OSError as exc:
        raise ProjectNotFoundError(f"{project_dir} could not be scanned: {exc}") from exc
    if newer:
        newest = max(newer, key=lambda f: newer[f])
        others = len(newer) - 1
        also = f" and {others} other file{'s' if others > 1 else ''}" if others else ""
        raise _stale(
            semantic_manifest, project_dir, f"is older than {_rel(newest, project_dir)}{also}"
        )

    project = load_project(project_dir)
    if not scan.warnings:
        # An unreadable file may declare what looks removed, so only check when all were read.
        _check_removed(project, scan, semantic_manifest)

    present = {sm.name.lower() for sm in project.semantic_manifest.semantic_models}
    warnings = list(scan.warnings)
    reported: set[str] = set()
    for declared in sorted(scan.declared_semantic_models, key=lambda d: (d.name, str(d.file))):
        if declared.name in present or declared.name in reported:
            continue
        reported.add(declared.name)
        warnings.append(
            f"semantic model {declared.name!r} is declared in {_rel(declared.file, project_dir)} "
            f"but missing from {SEMANTIC_MANIFEST} (produced by {project.producer})"
            f"{_missing_hint(declared, project.producer)}"
        )
    return ArtifactCheck(project=project, warnings=tuple(warnings))
