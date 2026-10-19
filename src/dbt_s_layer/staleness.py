"""
Check that a project's parse artifacts exist and match its semantic YAML (CLI-05).

The scan also collects the semantic models the YAML declares, so a semantic model
the engine silently left out of semantic_manifest.json is reported. dbt Core
1.12.6 does this for semantic models on versioned models, and Fusion for every
legacy-spec `semantic_models:` entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_s_layer._project_yaml import (
    DeclaredSemanticModel,
    SemanticYamlScan,
    _rel,
    scan_semantic_yaml,
)
from dbt_s_layer.artifacts import (
    SEMANTIC_MANIFEST,
    missing_artifact_error,
    resolve_target_dir,
)
from dbt_s_layer.errors import ProjectNotFoundError, StaleArtifactError
from dbt_s_layer.project import SemanticProject, load_project

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ArtifactCheck",
    "DeclaredSemanticModel",
    "SemanticYamlScan",
    "check_artifacts",
    "scan_semantic_yaml",
]


@dataclass(frozen=True)
class ArtifactCheck:
    """A loaded project whose artifacts are fresh, and anything worth warning about."""

    project: SemanticProject
    warnings: tuple[str, ...]
    # The warnings that say a semantic model the YAML declares is missing from
    # semantic_manifest.json (also in `warnings`).
    missing: tuple[str, ...] = ()


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
    missing: list[str] = []
    reported: set[str] = set()
    for declared in sorted(scan.declared_semantic_models, key=lambda d: (d.name, str(d.file))):
        if declared.name in present or declared.name in reported:
            continue
        reported.add(declared.name)
        missing.append(
            f"semantic model {declared.name!r} is declared in {_rel(declared.file, project_dir)} "
            f"but missing from {SEMANTIC_MANIFEST} (produced by {project.producer})"
            f"{_missing_hint(declared, project.producer)}"
        )
    return ArtifactCheck(project=project, warnings=(*warnings, *missing), missing=tuple(missing))
