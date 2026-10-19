"""Load a dbt project's parse artifacts into one typed `SemanticProject`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_s_layer.artifacts import (
    DBT_MANIFEST,
    SEMANTIC_MANIFEST,
    DbtManifest,
    read_dbt_manifest,
    read_dbt_project,
    read_semantic_manifest,
    resolve_target_dir,
)
from dbt_s_layer.errors import ArtifactInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from metricflow_semantic_interfaces.implementations.semantic_manifest import (
        PydanticSemanticManifest,
    )

_MAJOR_RE = re.compile(r"^(\d+)\.")


def producer_label(dbt_version: str) -> str:
    """
    Name the engine that wrote the artifacts, from manifest.json `metadata.dbt_version`.

    Fusion reports 2.x, dbt Core 1.x.
    """
    match = _MAJOR_RE.match(dbt_version)
    if match is None:
        return f"dbt {dbt_version}"
    kind = "Fusion" if int(match.group(1)) >= 2 else "dbt Core"
    return f"{kind} {dbt_version}"


@dataclass(frozen=True)
class SemanticProject:
    """
    A dbt project's semantic manifest and the dbt manifest slice it links to.

    A dataclass rather than a pydantic model: it holds metricflow's pydantic v1
    manifest, and pydantic v2 models must not embed v1 ones.
    """

    project_dir: Path
    # metricflow's manifest, already transformed and validated.
    semantic_manifest: PydanticSemanticManifest
    dbt_manifest: DbtManifest
    # e.g. "Fusion 2.0.8" or "dbt Core 1.12.6"
    producer: str


def load_project(project_dir: Path) -> SemanticProject:
    """Read `project_dir`'s parse artifacts from its target directory."""
    project_dir = project_dir.resolve()
    target_dir = resolve_target_dir(project_dir)
    semantic_manifest = read_semantic_manifest(
        target_dir / SEMANTIC_MANIFEST, project_dir=project_dir
    )
    dbt_manifest = read_dbt_manifest(target_dir / DBT_MANIFEST, project_dir=project_dir)

    # A manifest.json left over from another project would map semantic models
    # to the wrong nodes.
    project_name = read_dbt_project(project_dir).get("name")
    if dbt_manifest.metadata.project_name != project_name:
        raise ArtifactInvalidError(
            f"{target_dir / DBT_MANIFEST} is for dbt project "
            f"{dbt_manifest.metadata.project_name!r}, but {project_dir} is project "
            f"{project_name!r}. Re-run `dbt parse` in {project_dir}."
        )
    return SemanticProject(
        project_dir=project_dir,
        semantic_manifest=semantic_manifest,
        dbt_manifest=dbt_manifest,
        producer=producer_label(dbt_manifest.metadata.dbt_version),
    )
