"""
Errors dbt-s-layer reports to the user.

Every error here carries a complete, one-line-friendly message: the CLI prints
`str(error)` and exits 1, with no traceback.
"""

from __future__ import annotations


class DbtSLayerError(Exception):
    """Base class for errors with a user-facing message."""


class ProjectNotFoundError(DbtSLayerError):
    """The directory is not a dbt project, or its dbt_project.yml cannot be read."""


class ArtifactNotFoundError(DbtSLayerError):
    """A dbt parse artifact (semantic_manifest.json, manifest.json) does not exist."""


class ArtifactInvalidError(DbtSLayerError):
    """A dbt parse artifact exists but cannot be read or is rejected by metricflow."""


class StaleArtifactError(DbtSLayerError):
    """semantic_manifest.json is older than the semantic YAML it was parsed from."""


class ArtifactIncompleteError(DbtSLayerError):
    """semantic_manifest.json lacks a semantic model the project's YAML declares."""


class RefResolutionError(DbtSLayerError):
    """A semantic model cannot be mapped to exactly one dbt model node."""


class SentinelCollisionError(DbtSLayerError):
    """Project input already contains a reserved sentinel prefix (`__REF__` and friends)."""


class ExportRenderError(DbtSLayerError):
    """An export's SQL could not be rendered into a dbt model."""


class OutputConflictError(DbtSLayerError):
    """The output directory holds a file dbt-s-layer would overwrite but did not generate."""


class ProfileError(DbtSLayerError):
    """A dbt target's adapter type cannot be read from profiles.yml."""


class ModelClashError(DbtSLayerError):
    """A generated model would clash with one of the user's own models."""


class SemanticViewError(DbtSLayerError):
    """A semantic model cannot be turned into a semantic view as it stands."""


class SLayerBlockError(DbtSLayerError):
    """A `config.meta.s_layer` block is not valid S-LAYER vocabulary for its member."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        # One entry per problem, each naming its member.
        self.problems = problems or (message,)
