"""Load a dbt project's parse artifacts into one typed `SemanticProject`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from metricflow_semantic_interfaces.implementations.element_config import (
    PydanticSemanticLayerElementConfig,
)

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
    producer = producer_label(dbt_manifest.metadata.dbt_version)
    _merge_column_meta(semantic_manifest, dbt_manifest, producer)
    return SemanticProject(
        project_dir=project_dir,
        semantic_manifest=semantic_manifest,
        dbt_manifest=dbt_manifest,
        producer=producer,
    )


def _merge_column_meta(
    semantic_manifest: PydanticSemanticManifest, dbt_manifest: DbtManifest, producer: str
) -> None:
    """
    Give every dimension the `config.meta` dbt Core would, whichever engine parsed.

    dbt Core merges a column's `config.meta` into the new-spec dimension declared on
    it, the dimension's own keys winning. Fusion writes only the dimension's own
    meta, or `config: null`. manifest.json keeps the column meta on both engines, so
    for Fusion the merge is redone from it here (Fusion reads no legacy-spec semantic
    models, so every one is new-spec). Dimensions without a config get an empty one.
    """
    fusion = producer.startswith("Fusion")
    for semantic_model in semantic_manifest.semantic_models:
        column_meta = _column_meta(dbt_manifest, semantic_model.name) if fusion else {}
        for dimension in semantic_model.dimensions:
            own = {} if dimension.config is None else dimension.config.meta
            merged = {**column_meta.get(dimension.name, {}), **own}
            dimension.config = PydanticSemanticLayerElementConfig(meta=merged)


def _column_meta(dbt_manifest: DbtManifest, semantic_model: str) -> dict[str, dict[str, object]]:
    """Map each dimension declared on a column of the semantic model's node to its meta."""
    matches = [
        sm for sm in dbt_manifest.semantic_models.values() if sm.name.lower() == semantic_model
    ]
    # Missing or ambiguous: `refs.build_ref_map` reports it, so don't guess here.
    if len(matches) != 1:
        return {}
    meta: dict[str, dict[str, object]] = {}
    for node_id in matches[0].depends_on.nodes:
        node = dbt_manifest.nodes.get(node_id)
        if node is None:
            continue
        for column in node.columns.values():
            name = column.dimension_name
            if name is not None:
                meta[name.lower()] = column.config.meta
    return meta
