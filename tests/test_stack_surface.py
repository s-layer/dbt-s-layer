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


def test_plan_renderers_expose_the_column_hooks_quoting_overrides() -> None:
    """`dbt_s_layer.quoting` overrides these; a renamed one would silently stop quoting."""
    import inspect

    from metricflow.sql.render.expr_renderer import DefaultSqlExpressionRenderer
    from metricflow.sql.render.sql_plan_renderer import DefaultSqlPlanRenderer

    select = inspect.signature(DefaultSqlPlanRenderer._render_select_columns_section)  # pyright: ignore[reportPrivateUsage]
    assert list(select.parameters) == ["self", "select_columns", "num_parents", "distinct"]
    for name in (
        "visit_column_reference_expr",
        "visit_column_alias_reference_expr",
        "render_group_by_expr",
    ):
        assert callable(getattr(DefaultSqlExpressionRenderer, name))


def test_engine_planner_attributes_used_by_the_shared_set_up() -> None:
    """
    `exports` gives each saved query a fresh planner on one engine's set-up (05-RESEARCH).

    It reads these private attributes of the pinned metricflow and replaces the planner ones.
    """
    import shutil
    import tempfile

    from metricflow.dataflow.builder.builder_cache import DataflowPlanBuilderCache
    from metricflow.dataflow.builder.dataflow_plan_builder import DataflowPlanBuilder
    from metricflow.engine.metricflow_engine import MetricFlowEngine
    from metricflow.plan_conversion.to_sql_plan.dataflow_to_sql import (
        DataflowToSqlPlanConverter,
    )
    from metricflow.plan_conversion.to_sql_plan.dataflow_to_subquery import (
        DataflowNodeToSqlSubqueryVisitor,
    )
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

    from dbt_s_layer.exports import sql_client_for
    from dbt_s_layer.project import load_project
    from tests.conftest import ARTIFACTS, PROJECTS

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "p"
        shutil.copytree(
            PROJECTS / "export_legacy", project_dir, ignore=shutil.ignore_patterns("target")
        )
        (project_dir / "target").mkdir()
        for name in ("semantic_manifest.json", "manifest.json"):
            shutil.copyfile(ARTIFACTS / "core_export" / name, project_dir / "target" / name)
        project = load_project(project_dir)
    engine = MetricFlowEngine(
        SemanticManifestLookup(project.semantic_manifest), sql_client_for("duckdb")
    )
    assert isinstance(engine._dataflow_plan_builder, DataflowPlanBuilder)
    assert isinstance(engine._dataflow_plan_builder_cache, DataflowPlanBuilderCache)
    assert isinstance(engine._to_sql_plan_converter, DataflowToSqlPlanConverter)
    assert isinstance(engine._semantic_manifest_lookup, SemanticManifestLookup)
    assert engine._column_association_resolver is not None
    builder = engine._dataflow_plan_builder
    assert builder._source_node_set is not None
    assert builder._source_node_builder is not None
    assert isinstance(builder._node_data_set_resolver, DataflowNodeToSqlSubqueryVisitor)
    assert isinstance(builder._node_data_set_resolver.copy(), DataflowNodeToSqlSubqueryVisitor)
