"""
Output-directory defaults shared by the CLI and `doctor`.

Kept free of metricflow imports, so `doctor` can still run, and report a broken
install, when metricflow is missing.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("models") / "semantic"


def resolve_output_dir(project_dir: Path, output_dir: Path) -> Path:
    """A relative output directory is relative to the project, like dbt's own paths."""
    return (project_dir / output_dir).resolve()
