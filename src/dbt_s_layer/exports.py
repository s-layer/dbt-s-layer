"""
Render one dbt model body per saved-query export (EXPRT-01, EXPRT-03).

metricflow renders each saved query against a copy of the semantic manifest
whose relations are sentinel tokens (see `sentinels`), so no warehouse is
needed: the SQL client only names the dialect. Bodies are returned in memory;
Phase 3 writes them to disk.
"""

from __future__ import annotations

import copy
import multiprocessing
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from metricflow.dataflow.builder.builder_cache import DataflowPlanBuilderCache
from metricflow.dataflow.builder.dataflow_plan_builder import DataflowPlanBuilder
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.plan_conversion.to_sql_plan.dataflow_to_sql import DataflowToSqlPlanConverter
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.athena import AthenaSqlPlanRenderer
from metricflow.sql.render.big_query import BigQuerySqlPlanRenderer
from metricflow.sql.render.databricks import DatabricksSqlPlanRenderer
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer
from metricflow.sql.render.redshift import RedshiftSqlPlanRenderer
from metricflow.sql.render.snowflake import SnowflakeSqlPlanRenderer
from metricflow.sql.render.trino import TrinoSqlPlanRenderer
from metricflow_semantics.errors.error_classes import MetricFlowException
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_s_layer.errors import ExportRenderError
from dbt_s_layer.quoting import quoting_renderer
from dbt_s_layer.sentinels import build_sentinel_table, postflight, rewrite

if TYPE_CHECKING:
    from collections.abc import Sequence

    from metricflow.data_table.mf_table import MetricFlowDataTable
    from metricflow.sql.render.sql_plan_renderer import DefaultSqlPlanRenderer, SqlPlanRenderer
    from metricflow_semantic_interfaces.implementations.export import PydanticExport
    from metricflow_semantic_interfaces.implementations.saved_query import PydanticSavedQuery
    from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

    from dbt_s_layer.artifacts import DbtSavedQuery
    from dbt_s_layer.project import SemanticProject

Materialization = Literal["table", "view"]

# Export names become part of a file name and a dbt model name, and metricflow
# does not validate them (it does validate saved query names).
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
_JINJA_RE = re.compile(r"\{[{%]")

# dbt adapter_type -> metricflow dialect.
_DIALECTS: dict[str, tuple[SqlEngine, type[DefaultSqlPlanRenderer]]] = {
    "athena": (SqlEngine.ATHENA, AthenaSqlPlanRenderer),
    "bigquery": (SqlEngine.BIGQUERY, BigQuerySqlPlanRenderer),
    "databricks": (SqlEngine.DATABRICKS, DatabricksSqlPlanRenderer),
    "duckdb": (SqlEngine.DUCKDB, DuckDbSqlPlanRenderer),
    "postgres": (SqlEngine.POSTGRES, PostgresSQLSqlPlanRenderer),
    "redshift": (SqlEngine.REDSHIFT, RedshiftSqlPlanRenderer),
    "snowflake": (SqlEngine.SNOWFLAKE, SnowflakeSqlPlanRenderer),
    "trino": (SqlEngine.TRINO, TrinoSqlPlanRenderer),
}
SUPPORTED_ADAPTERS = tuple(_DIALECTS)

# Explaining saved queries in a process pool (05-RESEARCH finding 4).
JOBS_ENV = "DBT_S_LAYER_JOBS"
_PARALLEL_MIN_SAVED_QUERIES = 8
_MAX_JOBS = 8


@dataclass(frozen=True)
class ExportModel:
    """One generated export model: where it goes and what it says."""

    saved_query: str
    export: str
    # exp__<saved_query>__<export>.sql
    file_name: str
    materialized: Materialization
    alias: str
    # Only when the user set one; otherwise the model gets the target schema.
    schema: str | None
    # `{{ config(...) }}`, a blank line, the SQL, and a final newline.
    body: str


class _RenderOnlySqlClient:
    """A metricflow `SqlClient` that can render SQL for a dialect but never run it."""

    def __init__(self, engine: SqlEngine, renderer: SqlPlanRenderer) -> None:
        self._engine = engine
        self._renderer = renderer

    @property
    def sql_engine_type(self) -> SqlEngine:
        return self._engine

    @property
    def sql_plan_renderer(self) -> SqlPlanRenderer:
        return self._renderer

    def _refuse(self) -> ExportRenderError:
        return ExportRenderError("dbt-s-layer renders SQL only; it never runs a query.")

    def query(
        self, stmt: str, sql_bind_parameter_set: SqlBindParameterSet | None = None
    ) -> MetricFlowDataTable:
        raise self._refuse()

    def execute(self, stmt: str, sql_bind_parameter_set: SqlBindParameterSet | None = None) -> None:
        raise self._refuse()

    def dry_run(self, stmt: str, sql_bind_parameter_set: SqlBindParameterSet | None = None) -> None:
        raise self._refuse()

    def close(self) -> None:
        return None

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        raise self._refuse()


def sql_client_for(adapter: str) -> _RenderOnlySqlClient:
    """Return a render-only metricflow SQL client for a dbt adapter type."""
    dialect = _DIALECTS.get(adapter)
    if dialect is None:
        raise ExportRenderError(
            f"adapter {adapter!r} is not supported for exports; "
            f"supported: {', '.join(SUPPORTED_ADAPTERS)}."
        )
    engine, renderer = dialect
    return _RenderOnlySqlClient(engine, quoting_renderer(adapter, renderer)())


def _one_line(exc: BaseException) -> str:
    text = " ".join(line.strip() for line in str(exc).splitlines() if line.strip())
    return text or type(exc).__name__


def _dbt_saved_query(project: SemanticProject, name: str) -> DbtSavedQuery:
    matches = [sq for sq in project.dbt_manifest.saved_queries.values() if sq.name.lower() == name]
    if len(matches) != 1:
        found = "is not" if not matches else "is more than once"
        raise ExportRenderError(
            f"saved query {name!r} is in semantic_manifest.json but {found} in manifest.json "
            f"(artifacts produced by {project.producer}). Re-run `dbt parse`."
        )
    return matches[0]


def _materialized(
    dbt_sq: DbtSavedQuery, export: PydanticExport
) -> tuple[Materialization, str | None]:
    """
    Resolve `export_as` and `schema` from what the user wrote.

    The export's own config wins over the saved query's. Read from manifest.json
    because Fusion 2.0.8 does not apply a saved query's `export_as` to its exports.
    `unrendered_config` holds the user's text, Jinja included, so a templated
    value is taken from the rendered config instead.
    """
    entries = [entry for entry in dbt_sq.exports if entry.name == export.name]
    if len(entries) > 1:
        raise ExportRenderError(
            f"saved query {dbt_sq.name!r} has more than one export named {export.name!r}. "
            "Rename one of them."
        )
    if not entries:
        raise ExportRenderError(
            f"export {export.name!r} of saved query {dbt_sq.name!r} is in "
            "semantic_manifest.json but not in manifest.json. Re-run `dbt parse`."
        )
    unrendered = entries[0].unrendered_config
    rendered = {
        "export_as": export.config.export_as.value,
        "schema": export.config.schema_name,
    }

    def own(key: str) -> object:
        value = unrendered.get(key)
        if isinstance(value, str) and _JINJA_RE.search(value):
            return rendered[key]
        return value

    export_as = own("export_as") or dbt_sq.config.export_as or "table"
    if export_as not in ("table", "view"):
        raise ExportRenderError(
            f"export {export.name!r} of saved query {dbt_sq.name!r} sets export_as "
            f"{export_as!r}; dbt-s-layer generates 'table' and 'view' exports."
        )
    schema = own("schema") or dbt_sq.config.schema_name
    return export_as, schema if isinstance(schema, str) else None


def _config_block(materialized: str, alias: str, schema: str | None) -> str:
    args = [f"materialized={materialized!r}", f"alias={alias!r}"]
    if schema is not None:
        args.append(f"schema={schema!r}")
    return "{{ config(" + ", ".join(args) + ") }}"


def _export_model(
    saved_query: PydanticSavedQuery,
    export: PydanticExport,
    dbt_sq: DbtSavedQuery,
    sql: str,
) -> ExportModel:
    if not _SAFE_NAME_RE.fullmatch(export.name):
        raise ExportRenderError(
            f"export {export.name!r} of saved query {saved_query.name!r}: export names may "
            "only contain ASCII letters, digits and underscores, because they name the "
            "generated file and dbt model."
        )
    materialized, schema = _materialized(dbt_sq, export)
    alias = export.config.alias or export.name
    file_name = f"exp__{saved_query.name}__{export.name}.sql"
    body = f"{_config_block(materialized, alias, schema)}\n\n{sql}\n"
    postflight(body, context=file_name)
    return ExportModel(
        saved_query=saved_query.name,
        export=export.name,
        file_name=file_name,
        materialized=materialized,
        alias=alias,
        schema=schema,
        body=body,
    )


class _SharedEngine:
    """
    One `MetricFlowEngine` set-up that hands out an engine with a fresh planner per query.

    The set-up (source data sets and source nodes for every semantic model) is the
    expensive part and no query changes it. The planner's caches are what make an
    engine render a query it has planned before with different aliases (02-RESEARCH
    finding 1), so each query gets new ones (05-RESEARCH finding 3). Uses private
    attributes of the pinned metricflow, guarded by tests/test_stack_surface.py.
    """

    def __init__(self, lookup: SemanticManifestLookup, client: _RenderOnlySqlClient) -> None:
        self._base = MetricFlowEngine(lookup, client)

    def engine(self) -> MetricFlowEngine:
        base = self._base
        builder = base._dataflow_plan_builder
        engine = copy.copy(base)
        engine._dataflow_plan_builder_cache = DataflowPlanBuilderCache()
        engine._dataflow_plan_builder = DataflowPlanBuilder(
            source_node_set=builder._source_node_set,
            semantic_manifest_lookup=base._semantic_manifest_lookup,
            column_association_resolver=base._column_association_resolver,
            # The base engine never plans a query, so this holds the source nodes only.
            node_output_resolver=builder._node_data_set_resolver.copy(),
            source_node_builder=builder._source_node_builder,
            dataflow_plan_builder_cache=engine._dataflow_plan_builder_cache,
        )
        engine._to_sql_plan_converter = DataflowToSqlPlanConverter(
            column_association_resolver=base._column_association_resolver,
            semantic_manifest_lookup=base._semantic_manifest_lookup,
        )
        return engine


@dataclass(frozen=True)
class _Explained:
    """metricflow's SQL for one saved query, or why there is none."""

    sql: str | None
    error: str | None


def _explain(shared: _SharedEngine, saved_query: str) -> _Explained:
    context = f"saved query {saved_query!r}"
    request = MetricFlowQueryRequest.create(saved_query_name=saved_query)
    try:
        statement = shared.engine().explain(request).sql_statement
    except (MetricFlowException, NotImplementedError) as exc:
        return _Explained(None, f"{context}: metricflow could not render it: {_one_line(exc)}")
    if statement.bind_parameter_set.param_items:
        return _Explained(
            None,
            f"{context}: metricflow rendered SQL with bind parameters, which a dbt "
            "model cannot carry.",
        )
    return _Explained(statement.sql, None)


# Set in the parent before forking, so workers inherit the set-up instead of pickling it.
_worker_engine: _SharedEngine | None = None


def _explain_in_worker(saved_query: str) -> _Explained:
    assert _worker_engine is not None
    return _explain(_worker_engine, saved_query)


def _can_fork() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


def worker_count(saved_queries: int) -> int:
    """
    How many processes explain `saved_queries` saved queries.

    `DBT_S_LAYER_JOBS` (a positive integer) sets it; otherwise one per CPU, at most
    8, when there are at least 8 saved queries. Always 1 without the `fork` start
    method (Windows), since a worker must inherit the engine set-up.
    """
    raw = os.environ.get(JOBS_ENV, "")
    if raw:
        requested = int(raw) if raw.isdigit() else 0
        if requested < 1:
            raise ExportRenderError(f"{JOBS_ENV} must be a positive integer, not {raw!r}.")
    elif saved_queries < _PARALLEL_MIN_SAVED_QUERIES:
        return 1
    else:
        requested = min(os.cpu_count() or 1, _MAX_JOBS)
    if not _can_fork():
        return 1
    return max(1, min(requested, saved_queries))


def _explain_all(
    shared: _SharedEngine, saved_queries: Sequence[str], jobs: int
) -> list[_Explained]:
    """Explain each saved query, in order; in a forked process pool when `jobs` > 1."""
    if jobs <= 1 or len(saved_queries) <= 1 or not _can_fork():
        return [_explain(shared, name) for name in saved_queries]
    global _worker_engine
    _worker_engine = shared
    try:
        with multiprocessing.get_context("fork").Pool(min(jobs, len(saved_queries))) as pool:
            return pool.map(_explain_in_worker, saved_queries, chunksize=1)
    finally:
        _worker_engine = None


def render_exports(
    project: SemanticProject, *, adapter: str | None = None, jobs: int | None = None
) -> tuple[ExportModel, ...]:
    """
    Render every saved-query export in the project, sorted by file name.

    `adapter` overrides manifest.json `metadata.adapter_type`, which picks the
    SQL dialect. `jobs` is the number of processes that explain saved queries
    (default: `worker_count`); the output does not depend on it.
    """
    if jobs is not None and jobs < 1:
        raise ValueError(f"jobs must be at least 1, not {jobs}")
    adapter = adapter or project.dbt_manifest.metadata.adapter_type
    if adapter is None:
        raise ExportRenderError(
            "manifest.json has no metadata.adapter_type, so the SQL dialect is unknown; "
            "pass the adapter explicitly."
        )
    client = sql_client_for(adapter)
    table = build_sentinel_table(project)
    try:
        lookup = SemanticManifestLookup(table.manifest)
    except MetricFlowException as exc:
        raise ExportRenderError(
            f"metricflow could not load the manifest: {_one_line(exc)}"
        ) from exc

    saved_queries = sorted(
        (sq for sq in table.manifest.saved_queries if sq.exports), key=lambda sq: sq.name
    )
    explained: list[_Explained] = []
    if saved_queries:
        names = [sq.name for sq in saved_queries]
        jobs = jobs if jobs is not None else worker_count(len(names))
        explained = _explain_all(_SharedEngine(lookup, client), names, jobs)

    models: dict[str, ExportModel] = {}
    # (schema, alias) -> model. dbt refuses two models on one relation, but
    # compares case-sensitively; Snowflake, DuckDB and Databricks fold unquoted
    # names, so different case still builds one table (03-RESEARCH finding 2).
    relations: dict[tuple[str | None, str], ExportModel] = {}
    for saved_query, result in zip(saved_queries, explained, strict=True):
        context = f"saved query {saved_query.name!r}"
        dbt_sq = _dbt_saved_query(project, saved_query.name)
        if result.error is not None:
            raise ExportRenderError(result.error)
        assert result.sql is not None
        sql = rewrite(result.sql, table, context=context)
        for export in saved_query.exports:
            model = _export_model(saved_query, export, dbt_sq, sql)
            other = models.get(model.file_name)
            if other is not None:
                raise ExportRenderError(
                    f"{model.file_name} would be written by both export {other.export!r} of "
                    f"saved query {other.saved_query!r} and export {model.export!r} of saved "
                    f"query {model.saved_query!r}. Rename one of them."
                )
            schema = model.schema.lower() if model.schema is not None else None
            relation = (schema, model.alias.lower())
            other = relations.get(relation)
            if other is not None:
                where = f"schema {model.schema!r}" if model.schema else "the target schema"
                raise ExportRenderError(
                    f"export {other.export!r} of saved query {other.saved_query!r} and export "
                    f"{model.export!r} of saved query {model.saved_query!r} would both build "
                    f"alias {model.alias!r} in {where}. Give one of them its own `alias`."
                )
            relations[relation] = model
            models[model.file_name] = model
    return tuple(models[name] for name in sorted(models))
