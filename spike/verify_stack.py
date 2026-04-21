"""Stack verification spike for dbt-s-layer Phase 0.

Run: uv run python spike/verify_stack.py
Commits a snapshot of what Phase 0 found so later phases can trust pins.

This script is import-only — it does NOT parse a real manifest, run dbtf,
or talk to the network. It verifies:

1. Resolved versions of metricflow, metricflow-semantics, dbt-semantic-interfaces, pydantic
2. That key import paths we rely on in later phases still exist:
   - dbt_semantic_interfaces.implementations.semantic_manifest.PydanticSemanticManifest
   - metricflow_semantics.sql.sql_table.SqlTable (+ from_string classmethod)
3. Whether PydanticSemanticManifest uses pydantic v1 or v2 semantics
   (model_fields vs __fields__) — cascades to our compat shim strategy
"""

from __future__ import annotations

import importlib.metadata as meta
import sys


def probe() -> int:
    """Report resolved versions and internal import paths we depend on.

    Returns:
        0 if all probes succeed, non-zero if any critical import fails.
    """
    exit_code = 0

    print("=" * 60)
    print("dbt-s-layer Phase 0 stack verification spike")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print()

    print("--- Resolved package versions ---")
    for pkg in ("metricflow", "metricflow-semantics", "dbt-semantic-interfaces", "pydantic"):
        try:
            print(f"  {pkg}: {meta.version(pkg)}")
        except meta.PackageNotFoundError:
            print(f"  {pkg}: NOT INSTALLED")
            exit_code = 1

    print()
    print("--- Import-surface checks ---")

    try:
        from dbt_semantic_interfaces.implementations.semantic_manifest import (
            PydanticSemanticManifest,
        )
        print(f"  PydanticSemanticManifest: OK -> {PydanticSemanticManifest!r}")
    except ImportError as exc:
        print(f"  PydanticSemanticManifest: FAIL -> {exc}")
        exit_code = 1
        PydanticSemanticManifest = None  # type: ignore[assignment]

    try:
        from metricflow_semantics.sql.sql_table import SqlTable
        print(f"  SqlTable: OK -> {SqlTable!r}")
        print(f"  SqlTable.from_string: OK -> {SqlTable.from_string!r}")
    except ImportError as exc:
        print(f"  SqlTable: FAIL -> {exc}")
        exit_code = 1

    print()
    print("--- Pydantic v1 vs v2 shape ---")
    try:
        import pydantic
        print(f"  pydantic.VERSION: {pydantic.VERSION}")
    except ImportError as exc:
        print(f"  pydantic: FAIL -> {exc}")
        exit_code = 1

    if PydanticSemanticManifest is not None:
        has_v2_fields = hasattr(PydanticSemanticManifest, "model_fields")
        has_v1_fields = hasattr(PydanticSemanticManifest, "__fields__")
        print(f"  manifest uses pydantic-v2 API (model_fields): {has_v2_fields}")
        print(f"  manifest uses pydantic-v1 API (__fields__):   {has_v1_fields}")
        if has_v1_fields and not has_v2_fields:
            print("  DECISION: dbt-s-layer must import via `from pydantic.v1 import ...`")
        elif has_v2_fields:
            print("  DECISION: dbt-s-layer can use pydantic v2 directly")

    print()
    print("=" * 60)
    print(f"Spike exit code: {exit_code}")
    print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(probe())
