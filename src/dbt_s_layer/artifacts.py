"""
Read the artifacts `dbt parse` writes: semantic_manifest.json and manifest.json.

dbt-s-layer never runs the parser itself (PARSE-01). It reads whatever dbt Core
(>= 1.7) or dbt Fusion left in the project's target directory. Both engines
write the same artifact format for both YAML specs, so nothing here branches on
engine or spec (PARSE-02).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, cast

from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantic_interfaces.validations.semantic_manifest_validator import (
    SemanticManifestValidator,
)
from metricflow_semantics.errors.error_classes import MetricFlowException
from metricflow_semantics.model.dbt_manifest_parser import (
    transform_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticV2ValidationError
from pydantic.v1 import ValidationError as PydanticV1ValidationError

from dbt_s_layer._yaml import YamlReadError, read_yaml
from dbt_s_layer.errors import ArtifactInvalidError, ArtifactNotFoundError, ProjectNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

SEMANTIC_MANIFEST = "semantic_manifest.json"
DBT_MANIFEST = "manifest.json"
DEFAULT_TARGET_PATH = "target"
_MAX_REPORTED_ISSUES = 5


# --- target directory -------------------------------------------------------


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


def resolve_target_dir(project_dir: Path) -> Path:
    """
    Return the directory `dbt parse` writes artifacts to, as dbt resolves it.

    `DBT_TARGET_PATH` wins over `target-path` in dbt_project.yml, which wins
    over the default `target`. A relative path is relative to the project.
    """
    config = read_dbt_project(project_dir)
    target_path = os.environ.get("DBT_TARGET_PATH") or config.get("target-path")
    if target_path is None:
        target_path = DEFAULT_TARGET_PATH
    if not isinstance(target_path, str) or not target_path:
        raise ProjectNotFoundError(
            f"{project_dir / 'dbt_project.yml'} could not be read: "
            "`target-path` must be a non-empty string."
        )
    if "{{" in target_path:
        raise ProjectNotFoundError(
            f"{project_dir / 'dbt_project.yml'} sets `target-path` with Jinja "
            f"({target_path!r}), which dbt-s-layer does not render. "
            "Set DBT_TARGET_PATH to the rendered path instead."
        )
    return project_dir / target_path


# --- shared reading ---------------------------------------------------------


def missing_artifact_error(path: Path, project_dir: Path) -> ArtifactNotFoundError:
    """Return the error for an artifact `dbt parse` has not written."""
    return ArtifactNotFoundError(
        f"{path} does not exist. Run `dbt parse` (dbt Core or Fusion) in {project_dir} first."
    )


def _read_json(path: Path, project_dir: Path) -> object:
    if not path.exists():
        raise missing_artifact_error(path, project_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ArtifactInvalidError(f"{path} could not be read: {exc}") from exc
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactInvalidError(
            f"{path} is not valid JSON ({exc}). Re-run `dbt parse` in {project_dir}."
        ) from exc
    return data


def _join_issues(issues: list[str]) -> str:
    shown = issues[:_MAX_REPORTED_ISSUES]
    more = len(issues) - len(shown)
    return "; ".join(shown) + (f"; and {more} more" if more > 0 else "")


def _loc(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc if part != "__root__") or "(top level)"


# --- semantic_manifest.json -------------------------------------------------


def read_semantic_manifest(path: Path, *, project_dir: Path) -> PydanticSemanticManifest:
    """
    Load semantic_manifest.json the way metricflow does for dbt-generated manifests.

    Loading goes through metricflow's dbt transformations (lower-cased names,
    legacy measures turned into metrics), then metricflow's validator, then a
    `SemanticManifestLookup`, so any manifest returned here can drive `explain()`.
    """
    data = _read_json(path, project_dir)
    try:
        raw = PydanticSemanticManifest.parse_obj(data)
    except PydanticV1ValidationError as exc:
        issues = [f"{_loc(err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise ArtifactInvalidError(
            f"{path} is not a semantic manifest metricflow can read: {_join_issues(issues)}"
        ) from exc
    manifest = transform_dbt_generated_manifest(raw)

    # metricflow's default-rules argument is typed against an unsolved TypeVar.
    validator = cast(
        "SemanticManifestValidator[PydanticSemanticManifest]", SemanticManifestValidator()
    )
    results = validator.validate_semantic_manifest(manifest)
    if results.has_blocking_issues:
        issues = [issue.as_readable_str() for issue in results.errors]
        raise ArtifactInvalidError(f"{path} was rejected by metricflow: {_join_issues(issues)}")
    try:
        SemanticManifestLookup(manifest)
    except MetricFlowException as exc:
        first_line = str(exc).strip().splitlines()[0]
        raise ArtifactInvalidError(f"{path} was rejected by metricflow: {first_line}") from exc
    return manifest


# --- manifest.json ----------------------------------------------------------


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class ManifestMetadata(_ManifestModel):
    """The `metadata` keys dbt-s-layer reads."""

    dbt_version: str
    project_name: str
    adapter_type: str | None = None


class DependsOn(_ManifestModel):
    """A `depends_on` block."""

    nodes: tuple[str, ...] = ()


class ModelNode(_ManifestModel):
    """A `nodes[*]` entry whose `resource_type` is `model`."""

    unique_id: str
    name: str
    package_name: str
    # dbt model versions may be numbers or strings (`v: 2`, `v: "beta"`).
    version: int | float | str | None = None
    database: str | None = None
    schema_name: str = Field(alias="schema")
    alias: str


class DbtSemanticModel(_ManifestModel):
    """A `semantic_models[*]` entry: links a semantic model to the node it reads."""

    unique_id: str
    name: str
    package_name: str
    # The user's own ref expression, e.g. `ref('orders')`.
    model: str | None = None
    depends_on: DependsOn = DependsOn()


class DbtManifest(_ManifestModel):
    """The slice of manifest.json dbt-s-layer reads."""

    metadata: ManifestMetadata
    nodes: dict[str, ModelNode]
    semantic_models: dict[str, DbtSemanticModel] = {}

    @field_validator("nodes", mode="before")
    @classmethod
    def _models_only(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        nodes = cast("dict[object, object]", value)
        models: dict[object, object] = {}
        for node_id, node in nodes.items():
            if (
                isinstance(node, dict)
                and cast("dict[object, object]", node).get("resource_type") == "model"
            ):
                models[node_id] = node
        return models


def read_dbt_manifest(path: Path, *, project_dir: Path) -> DbtManifest:
    """Load the parts of manifest.json dbt-s-layer needs."""
    data = _read_json(path, project_dir)
    try:
        return DbtManifest.model_validate(data)
    except PydanticV2ValidationError as exc:
        issues = [f"{_loc(err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise ArtifactInvalidError(
            f"{path} is not a dbt manifest dbt-s-layer can read: {_join_issues(issues)}"
        ) from exc
