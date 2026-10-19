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


class RefResolutionError(DbtSLayerError):
    """A semantic model cannot be mapped to exactly one dbt model node."""
