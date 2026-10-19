"""
Map each semantic model to the dbt model node it reads from (PARSE-03).

Phase 2's sentinel rewrite consumes this map: it swaps each semantic model's
physical relation for a token and later turns the token back into the user's
own `ref()` expression.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from dbt_s_layer.artifacts import RELATION_NODE_TYPES
from dbt_s_layer.errors import RefResolutionError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dbt_s_layer.artifacts import DbtSemanticModel
    from dbt_s_layer.project import SemanticProject


_NODE_PREFIXES = frozenset(RELATION_NODE_TYPES)


class RefTarget(BaseModel):
    """The dbt node (model, seed or snapshot) a semantic model reads, and how it was referenced."""

    model_config = ConfigDict(frozen=True)

    # Lowercased semantic model name, as metricflow names it.
    semantic_model: str
    node_id: str
    package: str
    model_name: str
    # dbt model versions may be numbers or strings; None for unversioned models.
    version: int | float | str | None
    # The user's own expression from manifest.json, e.g. `ref('orders')`.
    ref_expression: str
    # Physical relation parts from semantic_manifest.json `node_relation`.
    # Never parsed from `relation_name`, which dbt Core quotes and Fusion does not.
    database: str | None
    schema_name: str
    alias: str


def _find_dbt_semantic_model(project: SemanticProject, name: str) -> DbtSemanticModel:
    matches = [
        sm for sm in project.dbt_manifest.semantic_models.values() if sm.name.lower() == name
    ]
    if not matches:
        raise RefResolutionError(
            f"semantic model {name!r} is in semantic_manifest.json but not in manifest.json "
            f"(artifacts produced by {project.producer}). Re-run `dbt parse`."
        )
    if len(matches) > 1:
        ids = ", ".join(sorted(sm.unique_id for sm in matches))
        raise RefResolutionError(
            f"semantic model {name!r} is defined more than once in manifest.json ({ids}; "
            f"artifacts produced by {project.producer})."
        )
    return matches[0]


def build_ref_map(project: SemanticProject) -> Mapping[str, RefTarget]:
    """Return a read-only map of lowercased semantic model name -> `RefTarget`, sorted by name."""
    targets: dict[str, RefTarget] = {}
    for semantic_model in project.semantic_manifest.semantic_models:
        name = semantic_model.name.lower()
        dbt_sm = _find_dbt_semantic_model(project, name)
        model_ids = [n for n in dbt_sm.depends_on.nodes if n.split(".", 1)[0] in _NODE_PREFIXES]
        if len(model_ids) != 1:
            found = ", ".join(model_ids) if model_ids else "none"
            raise RefResolutionError(
                f"semantic model {name!r} must read exactly one dbt model, seed or snapshot, "
                f"but manifest.json lists {found} (artifacts produced by {project.producer})."
            )
        node = project.dbt_manifest.nodes.get(model_ids[0])
        if node is None:
            raise RefResolutionError(
                f"semantic model {name!r} reads {model_ids[0]}, which is not a node in "
                f"manifest.json (artifacts produced by {project.producer}). Re-run `dbt parse`."
            )
        if not dbt_sm.model:
            raise RefResolutionError(
                f"semantic model {name!r} has no ref() expression in manifest.json "
                f"(artifacts produced by {project.producer})."
            )
        relation = semantic_model.node_relation
        targets[name] = RefTarget(
            semantic_model=name,
            node_id=node.unique_id,
            package=node.package_name,
            model_name=node.name,
            version=node.version,
            ref_expression=dbt_sm.model,
            database=relation.database,
            schema_name=relation.schema_name,
            alias=relation.alias,
        )
    return MappingProxyType(dict(sorted(targets.items())))
