"""
Guard the metricflow surfaces later phases depend on.

These mirror `spike/verify_stack.py` so CI fails if a metricflow bump moves or
reshapes any of them. Update `spike/results-*.txt` alongside any change here.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pydantic
import pydantic.v1

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pinned_metricflow() -> str:
    deps: list[str] = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    pins = [d for d in deps if d.startswith("metricflow==")]
    assert len(pins) == 1, f"expected exactly one exact metricflow pin, got {pins}"
    return pins[0].removeprefix("metricflow==")


def test_installed_metricflow_matches_pin() -> None:
    """The environment under test runs the exact metricflow the package pins."""
    assert version("metricflow") == _pinned_metricflow()


def test_manifest_model_is_pydantic_v1() -> None:
    """PydanticSemanticManifest is a pydantic.v1 model, so our code must use the v1 shim."""
    from metricflow_semantic_interfaces.implementations.semantic_manifest import (
        PydanticSemanticManifest,
    )

    assert issubclass(PydanticSemanticManifest, pydantic.v1.BaseModel)
    assert not issubclass(PydanticSemanticManifest, pydantic.BaseModel)


def test_sql_table_from_string_parses_three_part_name() -> None:
    """SqlTable.from_string splits db.schema.table, which the sentinel rewrite relies on."""
    from metricflow_semantics.sql.sql_table import SqlTable

    table = SqlTable.from_string("analytics.marts.__REF__orders")
    assert table.db_name == "analytics"
    assert table.schema_name == "marts"
    assert table.table_name == "__REF__orders"
    assert table.sql == "analytics.marts.__REF__orders"


def test_sql_table_from_string_parses_two_part_name() -> None:
    """A two-part name leaves db_name unset."""
    from metricflow_semantics.sql.sql_table import SqlTable

    table = SqlTable.from_string("marts.orders")
    assert table.db_name is None
    assert table.schema_name == "marts"
    assert table.table_name == "orders"


def test_metricflow_engine_exposes_explain() -> None:
    """MetricFlowEngine.explain is the export SQL entry point for Phase 2."""
    from metricflow.engine.metricflow_engine import MetricFlowEngine

    assert callable(MetricFlowEngine.explain)
