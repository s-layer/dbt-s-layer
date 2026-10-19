"""
Render one dbt model body per saved-query export (EXPRT-01, EXPRT-03).

metricflow renders each saved query against a copy of the semantic manifest
whose relations are sentinel tokens (see `sentinels`), so no warehouse is
needed: the SQL client only names the dialect. Bodies are returned in memory;
Phase 3 writes them to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
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
from dbt_s_layer.sentinels import build_sentinel_table, postflight, rewrite

if TYPE_CHECKING:
    from metricflow.data_table.mf_table import MetricFlowDataTable
    from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer
    from metricflow_semantic_interfaces.implementations.export import PydanticExport
    from metricflow_semantic_interfaces.implementations.saved_query import PydanticSavedQuery
    from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

    from dbt_s_layer.artifacts import DbtSavedQuery
    from dbt_s_layer.project import SemanticProject

_SHARED: dict[int, MetricFlowEngine] = {}
Materialization = Literal["table", "view"]

# Export names become part of a file name and a dbt model name, and metricflow
# does not validate them (it does validate saved query names).
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

# dbt adapter_type -> metricflow dialect.
_DIALECTS: dict[str, tuple[SqlEngine, type[SqlPlanRenderer]]] = {
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
    return _RenderOnlySqlClient(engine, renderer())


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


def _materialized(dbt_sq: DbtSavedQuery, export: str) -> tuple[Materialization, str | None]:
    """
    Resolve `export_as` and `schema` from what the user wrote.

    The export's own config wins over the saved query's. Read from manifest.json
    because Fusion 2.0.8 does not apply a saved query's `export_as` to its exports.
    """
    unrendered: dict[str, object] = {}
    for entry in dbt_sq.exports:
        if entry.name == export:
            unrendered = entry.unrendered_config
    export_as = unrendered.get("export_as") or dbt_sq.config.export_as or "table"
    if export_as not in ("table", "view"):
        raise ExportRenderError(
            f"export {export!r} of saved query {dbt_sq.name!r} sets export_as {export_as!r}; "
            "dbt-s-layer generates 'table' and 'view' exports."
        )
    schema = unrendered.get("schema") or dbt_sq.config.schema_name
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
    materialized, schema = _materialized(dbt_sq, export.name)
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


def render_exports(
    project: SemanticProject, *, adapter: str | None = None
) -> tuple[ExportModel, ...]:
    """
    Render every saved-query export in the project, sorted by file name.

    `adapter` overrides manifest.json `metadata.adapter_type`, which picks the
    SQL dialect.
    """
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

    models: dict[str, ExportModel] = {}
    for saved_query in sorted(table.manifest.saved_queries, key=lambda sq: sq.name):
        if not saved_query.exports:
            continue
        context = f"saved query {saved_query.name!r}"
        dbt_sq = _dbt_saved_query(project, saved_query.name)
        # A fresh engine per saved query: an engine caches plans and renders a
        # query it has seen before with different aliases (02-RESEARCH.md, finding 1).
        engine = MetricFlowEngine(lookup, client)
        try:
            statement = engine.explain(
                MetricFlowQueryRequest.create(saved_query_name=saved_query.name)
            ).sql_statement
        except (MetricFlowException, NotImplementedError) as exc:
            raise ExportRenderError(
                f"{context}: metricflow could not render it: {_one_line(exc)}"
            ) from exc
        if statement.bind_parameter_set.param_items:
            raise ExportRenderError(
                f"{context}: metricflow rendered SQL with bind parameters, which a dbt "
                "model cannot carry."
            )
        sql = rewrite(statement.sql, table, context=context)
        for export in saved_query.exports:
            model = _export_model(saved_query, export, dbt_sq, sql)
            other = models.get(model.file_name)
            if other is not None:
                raise ExportRenderError(
                    f"{model.file_name} would be written by both export {other.export!r} of "
                    f"saved query {other.saved_query!r} and export {model.export!r} of saved "
                    f"query {model.saved_query!r}. Rename one of them."
                )
            models[model.file_name] = model
    return tuple(models[name] for name in sorted(models))
