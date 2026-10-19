"""Tests for export model rendering (EXPRT-01, EXPRT-03)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import jinja2
import pytest
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_s_layer.errors import ExportRenderError
from dbt_s_layer.exports import SUPPORTED_ADAPTERS, ExportModel, render_exports, sql_client_for
from dbt_s_layer.project import load_project
from dbt_s_layer.sentinels import postflight
from tests.conftest import EXPORT_SETS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dbt_s_layer.project import SemanticProject
    from tests.conftest import MakeProject

# file name -> (saved query, export, materialized, alias, schema); the same for both engines.
EXPECTED = {
    "exp__cumulative_revenue__exp_cumulative.sql": (
        "cumulative_revenue",
        "exp_cumulative",
        "table",
        "exp_cumulative",
        None,
    ),
    # export_as and schema come from the saved query's own config.
    "exp__items_by_country__exp_items_by_country.sql": (
        "items_by_country",
        "exp_items_by_country",
        "view",
        "exp_items_by_country",
        "sq_schema",
    ),
    "exp__revenue_by_day__exp_revenue_by_day.sql": (
        "revenue_by_day",
        "exp_revenue_by_day",
        "table",
        "exp_revenue_by_day",
        None,
    ),
    "exp__revenue_by_day__exp_revenue_by_day_view.sql": (
        "revenue_by_day",
        "exp_revenue_by_day_view",
        "view",
        "revenue_daily",
        "reporting",
    ),
    "exp__revenue_by_region_country__exp_region_country.sql": (
        "revenue_by_region_country",
        "exp_region_country",
        "table",
        "exp_region_country",
        None,
    ),
}

REVENUE_BY_DAY = """\
{{ config(materialized='table', alias='exp_revenue_by_day') }}

-- Read Elements From Semantic Model 'orders'
-- Metric Time Dimension 'ordered_at'
-- Select: ['__revenue', 'order__ordered_at__day']
-- Select: ['__revenue', 'order__ordered_at__day']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  DATE_TRUNC('day', ordered_at) AS order__ordered_at__day
  , SUM(amount) AS revenue
FROM {{ ref('orders') }} orders_src_10000
GROUP BY
  DATE_TRUNC('day', ordered_at)
"""


def _mutate(
    make_project: MakeProject,
    set_name: str,
    semantic: Callable[[dict[str, Any]], None] | None = None,
    dbt: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    project_dir = make_project(set_name)
    _rewrite(project_dir, semantic, dbt)
    return project_dir


def _rewrite(
    project_dir: Path,
    semantic: Callable[[dict[str, Any]], None] | None = None,
    dbt: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    for file, mutate in (("semantic_manifest.json", semantic), ("manifest.json", dbt)):
        if mutate is None:
            continue
        path = project_dir / "target" / file
        raw: dict[str, Any] = json.loads(path.read_text())
        mutate(raw)
        path.write_text(json.dumps(raw))


def _by_file(models: tuple[ExportModel, ...]) -> dict[str, ExportModel]:
    return {m.file_name: m for m in models}


# --- the oracle ---------------------------------------------------------------


def _physical_sql(project: SemanticProject, saved_query: str, adapter: str) -> str:
    """Metricflow's own SQL for the saved query, against the physical tables."""
    engine = MetricFlowEngine(
        SemanticManifestLookup(project.semantic_manifest), sql_client_for(adapter)
    )
    request = MetricFlowQueryRequest.create(saved_query_name=saved_query)
    return engine.explain(request).sql_statement.sql


def _dbt_render(project: SemanticProject, body: str) -> str:
    """Render a model body as dbt would, resolving ref() through manifest.json."""
    root = project.dbt_manifest.metadata.project_name

    def ref(*args: str, v: int | float | str | None = None) -> str:
        package, name = (root, args[0]) if len(args) == 1 else (args[0], args[1])
        matches = [
            n
            for n in project.dbt_manifest.nodes.values()
            if n.package_name == package and n.name == name and (v is None or n.version == v)
        ]
        assert len(matches) == 1, (args, v)
        assert matches[0].relation_name is not None
        return matches[0].relation_name

    def config(**_: object) -> str:
        return ""

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    rendered = env.from_string(body).render(ref=ref, config=config)
    assert rendered.startswith("\n\n")
    return rendered[2:].removesuffix("\n")


def _poison_status(raw: dict[str, Any]) -> None:
    orders = next(sm for sm in raw["semantic_models"] if sm["name"] == "orders")
    status = next(d for d in orders["dimensions"] if d["name"] == "status")
    status["expr"] = "case when status = '{{ x }}' then '{%' else status end"


@pytest.mark.parametrize("set_name", EXPORT_SETS)
@pytest.mark.parametrize("hostile", [False, True], ids=["plain", "jinja-in-expr"])
def test_dbt_renders_each_model_to_metricflows_own_sql(
    set_name: str, hostile: bool, make_project: MakeProject
) -> None:
    project_dir = _mutate(make_project, set_name, _poison_status if hostile else None)
    project = load_project(project_dir)
    adapter = project.dbt_manifest.metadata.adapter_type
    assert adapter is not None
    models = render_exports(project)
    assert len(models) == len(EXPECTED)
    for model in models:
        expected = _physical_sql(project, model.saved_query, adapter)
        assert _dbt_render(project, model.body) == expected, model.file_name
    if hostile:
        region = _by_file(models)["exp__revenue_by_region_country__exp_region_country.sql"]
        assert "{{ '{{' }} x }}" in region.body


# --- the models ---------------------------------------------------------------


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_one_model_per_export_with_resolved_config(
    set_name: str, make_project: MakeProject
) -> None:
    models = render_exports(load_project(make_project(set_name)))
    assert [m.file_name for m in models] == sorted(EXPECTED)
    for model in models:
        assert (
            model.saved_query,
            model.export,
            model.materialized,
            model.alias,
            model.schema,
        ) == EXPECTED[model.file_name]


def test_revenue_by_day_body(make_project: MakeProject) -> None:
    models = _by_file(render_exports(load_project(make_project("core_export"))))
    assert models["exp__revenue_by_day__exp_revenue_by_day.sql"].body == REVENUE_BY_DAY


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_config_blocks(set_name: str, make_project: MakeProject) -> None:
    models = _by_file(render_exports(load_project(make_project(set_name))))
    first_lines = {name: m.body.split("\n", 1)[0] for name, m in models.items()}
    assert first_lines["exp__revenue_by_day__exp_revenue_by_day_view.sql"] == (
        "{{ config(materialized='view', alias='revenue_daily', schema='reporting') }}"
    )
    assert first_lines["exp__items_by_country__exp_items_by_country.sql"] == (
        "{{ config(materialized='view', alias='exp_items_by_country', schema='sq_schema') }}"
    )
    assert first_lines["exp__cumulative_revenue__exp_cumulative.sql"] == (
        "{{ config(materialized='table', alias='exp_cumulative') }}"
    )


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_models_use_every_ref_form(set_name: str, make_project: MakeProject) -> None:
    models = _by_file(render_exports(load_project(make_project(set_name))))
    version = 1 if set_name == "core_export" else 2
    region = models["exp__revenue_by_region_country__exp_region_country.sql"].body
    assert "{{ ref('fx_dep', 'stores') }}" in region
    assert f"{{{{ ref('customers', v={version}) }}}}" in region
    cumulative = models["exp__cumulative_revenue__exp_cumulative.sql"].body
    assert "{{ ref('metricflow_time_spine') }}" in cumulative
    items = models["exp__items_by_country__exp_items_by_country.sql"].body
    for ref in ("ref('order_items')", "ref('orders')", f"ref('customers', v={version})"):
        assert ref in items
    for model in models.values():
        assert model.body.endswith("\n")
        postflight(model.body, context=model.file_name)


@pytest.mark.parametrize("set_name", EXPORT_SETS)
@pytest.mark.parametrize("saved_query", sorted({sq for sq, *_ in EXPECTED.values()}))
def test_a_model_does_not_depend_on_other_saved_queries(
    set_name: str, saved_query: str, make_project: MakeProject
) -> None:
    # One metricflow engine renders a saved query differently depending on
    # what it rendered before, so each saved query needs its own engine.
    project_dir = make_project(set_name)
    full = _by_file(render_exports(load_project(project_dir)))

    def only_one(raw: dict[str, Any]) -> None:
        raw["saved_queries"] = [q for q in raw["saved_queries"] if q["name"] == saved_query]

    def only_one_dbt(raw: dict[str, Any]) -> None:
        raw["saved_queries"] = {
            k: v for k, v in raw["saved_queries"].items() if k.endswith(f".{saved_query}")
        }

    _rewrite(project_dir, only_one, only_one_dbt)
    alone = render_exports(load_project(project_dir))
    assert alone
    for model in alone:
        assert model.saved_query == saved_query
        assert model.body == full[model.file_name].body


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_identical_saved_queries_render_identical_sql(
    set_name: str, make_project: MakeProject
) -> None:
    # A metricflow engine caches plans: asked for the same query a second time,
    # it renders different subquery aliases. Each saved query needs its own engine.
    def copy(raw: dict[str, Any]) -> None:
        original = next(q for q in raw["saved_queries"] if q["name"] == "items_by_country")
        clone = json.loads(json.dumps(original))
        clone["name"] = "items_copy"
        # Its own alias, so the two exports do not build the same relation.
        clone["exports"][0]["config"]["alias"] = "items_copy"
        raw["saved_queries"].append(clone)

    def copy_dbt(raw: dict[str, Any]) -> None:
        clone = json.loads(json.dumps(raw["saved_queries"]["saved_query.fx.items_by_country"]))
        clone.update(name="items_copy", unique_id="saved_query.fx.items_copy")
        raw["saved_queries"]["saved_query.fx.items_copy"] = clone

    models = _by_file(render_exports(load_project(_mutate(make_project, set_name, copy, copy_dbt))))
    original = models["exp__items_by_country__exp_items_by_country.sql"].body
    assert "subq_" in original
    copy_body = models["exp__items_copy__exp_items_by_country.sql"].body
    assert copy_body.replace("alias='items_copy'", "alias='exp_items_by_country'") == original


@pytest.mark.parametrize("set_name", EXPORT_SETS)
def test_rendering_is_repeatable(set_name: str, make_project: MakeProject) -> None:
    project = load_project(make_project(set_name))
    assert render_exports(project) == render_exports(project)


# --- adapters -----------------------------------------------------------------


def test_adapter_override(make_project: MakeProject) -> None:
    project = load_project(make_project("core_export"))
    duckdb = _by_file(render_exports(project))
    snowflake = _by_file(render_exports(project, adapter="snowflake"))
    name = "exp__cumulative_revenue__exp_cumulative.sql"
    assert _dbt_render(project, snowflake[name].body) == _physical_sql(
        project, "cumulative_revenue", "snowflake"
    )
    assert set(duckdb) == set(snowflake)


def test_every_supported_adapter_renders(make_project: MakeProject) -> None:
    project = load_project(make_project("fusion_export"))
    assert {"snowflake", "duckdb", "databricks"} <= set(SUPPORTED_ADAPTERS)
    for adapter in SUPPORTED_ADAPTERS:
        assert len(render_exports(project, adapter=adapter)) == len(EXPECTED)


# --- reserved words as column names (Snowflake smoke run, review round 2) ------------

ITEMS = "exp__items_by_country__exp_items_by_country.sql"
# adapter -> how an entity named `order` must appear: quoted, in the case the
# dialect folds an unquoted name to, so the column keeps the name it would have had.
QUOTED_ORDER = {
    "athena": '"order"',
    "bigquery": "`order`",
    "databricks": "`order`",
    "duckdb": '"order"',
    "postgres": '"order"',
    "redshift": '"order"',
    "snowflake": '"ORDER"',
    "trino": '"order"',
}


@pytest.mark.parametrize("set_name", [*EXPORT_SETS, "core_snowflake", "fusion_snowflake"])
@pytest.mark.parametrize("adapter", sorted(QUOTED_ORDER))
def test_a_reserved_entity_name_is_quoted(
    adapter: str, set_name: str, make_project: MakeProject
) -> None:
    # Snowflake: "syntax error ... unexpected 'order'" on `orders_src_10000.order_id AS order`
    # and `subq_10.order`, because the entity `order` reaches the SQL as a bare name.
    assert set(QUOTED_ORDER) == set(SUPPORTED_ADAPTERS)
    project = load_project(make_project(set_name))
    body = _by_file(render_exports(project, adapter=adapter))[ITEMS].body
    quoted = QUOTED_ORDER[adapter]
    assert f"AS {quoted}\n" in body
    assert re.search(rf"= subq_\d+\.{re.escape(quoted)}\n", body), body
    assert "AS order\n" not in body
    assert ".order\n" not in body
    # Only the reserved name changes: unquoting it gives metricflow's own SQL back.
    plain = body.replace(quoted, "order")
    assert re.search(r"order_items_src_10000\.order_id = subq_\d+\.order\n", plain), plain


def _group_and_order_by_order(raw: dict[str, Any]) -> None:
    query = next(q for q in raw["saved_queries"] if q["name"] == "items_by_country")
    query["query_params"]["group_by"].append("Entity('order')")
    query["query_params"]["order_by"] = ["Entity('order')"]


@pytest.mark.parametrize("set_name", EXPORT_SETS)
@pytest.mark.parametrize("adapter", sorted(QUOTED_ORDER))
def test_a_reserved_entity_in_group_by_and_order_by_is_quoted(
    adapter: str, set_name: str, make_project: MakeProject
) -> None:
    # Grouping and ordering by the entity `order` makes metricflow refer to the
    # column by its bare alias: ORDER BY always (an alias reference), and GROUP BY
    # on BigQuery, which groups by alias rather than by expression.
    project = load_project(_mutate(make_project, set_name, _group_and_order_by_order))
    body = _by_file(render_exports(project, adapter=adapter))[ITEMS].body
    quoted = QUOTED_ORDER[adapter]
    # The outermost SELECT names the output column...
    assert f"\nSELECT\n  order_items_src_10000.order_id AS {quoted}\n" in body, body
    # ...groups by it...
    group_by = quoted if adapter == "bigquery" else "order_items_src_10000.order_id"
    assert f"\nGROUP BY\n  {group_by}\n" in body, body
    # ...and orders by its alias.
    assert body.endswith(f"\nORDER BY {quoted}\n"), body
    # The name never appears bare outside the comments metricflow writes.
    sql = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("--"))
    assert not re.search(r"(?<![\w\"`])order(?![\w\"`])", sql.replace(quoted, "_")), sql


def test_unsupported_adapter(make_project: MakeProject) -> None:
    project = load_project(make_project("core_export"))
    with pytest.raises(ExportRenderError, match=r"'sqlserver'.*snowflake"):
        render_exports(project, adapter="sqlserver")


def test_missing_adapter_type(make_project: MakeProject) -> None:
    def no_adapter(raw: dict[str, Any]) -> None:
        del raw["metadata"]["adapter_type"]

    project = load_project(_mutate(make_project, "core_export", dbt=no_adapter))
    with pytest.raises(ExportRenderError, match=r"adapter_type"):
        render_exports(project)
    assert render_exports(project, adapter="duckdb")


# --- errors -------------------------------------------------------------------


def test_invalid_saved_query_is_named(make_project: MakeProject) -> None:
    def ambiguous(raw: dict[str, Any]) -> None:
        sq = next(q for q in raw["saved_queries"] if q["name"] == "revenue_by_region_country")
        sq["query_params"]["metrics"] = ["revenue", "items_sold"]

    project = load_project(_mutate(make_project, "core_export", ambiguous))
    with pytest.raises(ExportRenderError, match=r"saved query 'revenue_by_region_country'"):
        render_exports(project)


def test_bind_parameters_are_refused(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metricflow.engine import metricflow_engine
    from metricflow_semantics.sql.sql_bind_parameters import SqlBindParameterSet

    explain = metricflow_engine.MetricFlowEngine.explain

    def with_params(self: Any, request: Any) -> Any:
        result = explain(self, request)
        statement = result.sql_statement
        params = SqlBindParameterSet.create_from_dict({"p": 1})
        fake_statement = type(statement)(sql=statement.sql, bind_parameter_set=params)

        class _Result:
            sql_statement = fake_statement

        return _Result()

    monkeypatch.setattr(metricflow_engine.MetricFlowEngine, "explain", with_params)
    project = load_project(make_project("core_export"))
    with pytest.raises(
        ExportRenderError, match=r"saved query 'cumulative_revenue'.*bind parameters"
    ):
        render_exports(project)


def test_saved_query_missing_from_manifest_json(make_project: MakeProject) -> None:
    def drop(raw: dict[str, Any]) -> None:
        del raw["saved_queries"]["saved_query.fx.cumulative_revenue"]

    project = load_project(_mutate(make_project, "core_export", dbt=drop))
    with pytest.raises(ExportRenderError, match=r"cumulative_revenue.*manifest\.json"):
        render_exports(project)


def test_unknown_export_as(make_project: MakeProject) -> None:
    def cached(raw: dict[str, Any]) -> None:
        export = raw["saved_queries"]["saved_query.fx.revenue_by_day"]["exports"][0]
        export["unrendered_config"]["export_as"] = "cache"

    project = load_project(_mutate(make_project, "core_export", dbt=cached))
    with pytest.raises(ExportRenderError, match=r"exp_revenue_by_day.*'cache'"):
        render_exports(project)


def test_duplicate_export_names(make_project: MakeProject) -> None:
    # Two exports of one saved query with the same name share a file name.
    def rename(raw: dict[str, Any]) -> None:
        sq = next(q for q in raw["saved_queries"] if q["name"] == "revenue_by_day")
        sq["exports"][1]["name"] = "exp_revenue_by_day"
        sq["exports"][1]["config"]["alias"] = "other_alias"

    project = load_project(_mutate(make_project, "core_export", rename))
    with pytest.raises(ExportRenderError, match=r"__exp_revenue_by_day\.sql.*both"):
        render_exports(project)


@pytest.mark.parametrize("name", ["../escape", "a/b", "with space", "Ünïcode", ""])
def test_export_names_must_make_safe_file_names(name: str, make_project: MakeProject) -> None:
    # metricflow validates saved query names but not export names, which become
    # part of a file name and a dbt model name.
    def rename(raw: dict[str, Any]) -> None:
        sq = next(q for q in raw["saved_queries"] if q["name"] == "revenue_by_day")
        sq["exports"][0]["name"] = name

    def rename_dbt(raw: dict[str, Any]) -> None:
        raw["saved_queries"]["saved_query.fx.revenue_by_day"]["exports"][0]["name"] = name

    project = load_project(_mutate(make_project, "core_export", rename, rename_dbt))
    with pytest.raises(ExportRenderError, match=r"revenue_by_day.*letters, digits and underscores"):
        render_exports(project)


# --- review round 1 -----------------------------------------------------------


def test_duplicate_relations_are_refused(make_project: MakeProject) -> None:
    # Different files, but both exports default their alias to `daily` in the
    # target schema, so dbt would refuse two models on one relation.
    def rename(raw: dict[str, Any]) -> None:
        for sq in raw["saved_queries"]:
            if sq["name"] in ("cumulative_revenue", "revenue_by_region_country"):
                sq["exports"][0]["name"] = "daily"
                sq["exports"][0]["config"]["alias"] = "daily"

    def rename_dbt(raw: dict[str, Any]) -> None:
        for key in ("cumulative_revenue", "revenue_by_region_country"):
            raw["saved_queries"][f"saved_query.fx.{key}"]["exports"][0]["name"] = "daily"

    project = load_project(_mutate(make_project, "core_export", rename, rename_dbt))
    with pytest.raises(ExportRenderError, match=r"cumulative_revenue.*region.*alias 'daily'"):
        render_exports(project)


def test_same_alias_in_different_schemas_is_allowed(make_project: MakeProject) -> None:
    def alias(raw: dict[str, Any]) -> None:
        for sq in raw["saved_queries"]:
            if sq["name"] == "items_by_country":
                sq["exports"][0]["config"]["alias"] = "exp_revenue_by_day"

    models = _by_file(render_exports(load_project(_mutate(make_project, "core_export", alias))))
    items = models["exp__items_by_country__exp_items_by_country.sql"]
    assert (items.alias, items.schema) == ("exp_revenue_by_day", "sq_schema")


def test_export_missing_from_manifest_json(make_project: MakeProject) -> None:
    def drop(raw: dict[str, Any]) -> None:
        raw["saved_queries"]["saved_query.fx.revenue_by_day"]["exports"].pop()

    project = load_project(_mutate(make_project, "core_export", dbt=drop))
    with pytest.raises(ExportRenderError, match=r"exp_revenue_by_day_view.*manifest\.json"):
        render_exports(project)


def test_jinja_in_unrendered_config_uses_the_rendered_value(make_project: MakeProject) -> None:
    # manifest.json keeps what the user typed; a templated value must come from
    # the rendered config, or `{{ env_var(...) }}` would land in config() as text.
    def templated(raw: dict[str, Any]) -> None:
        export = raw["saved_queries"]["saved_query.fx.revenue_by_day"]["exports"][1]
        export["unrendered_config"]["schema"] = "{{ env_var('EXPORT_SCHEMA') }}"
        export["unrendered_config"]["export_as"] = "{{ 'view' }}"

    models = _by_file(
        render_exports(load_project(_mutate(make_project, "core_export", dbt=templated)))
    )
    view = models["exp__revenue_by_day__exp_revenue_by_day_view.sql"]
    assert (view.materialized, view.schema) == ("view", "reporting")
    assert "env_var" not in view.body


# --- review round 2 -----------------------------------------------------------


def test_duplicate_export_names_in_both_artifacts(make_project: MakeProject) -> None:
    # What a user who names two exports alike actually gets: the duplicate is in
    # both artifacts, so re-running `dbt parse` cannot help and the error must say so.
    def rename(raw: dict[str, Any]) -> None:
        sq = next(q for q in raw["saved_queries"] if q["name"] == "revenue_by_day")
        sq["exports"][1]["name"] = "exp_revenue_by_day"
        sq["exports"][1]["config"]["alias"] = "other_alias"

    def rename_dbt(raw: dict[str, Any]) -> None:
        raw["saved_queries"]["saved_query.fx.revenue_by_day"]["exports"][1]["name"] = (
            "exp_revenue_by_day"
        )

    project = load_project(_mutate(make_project, "core_export", rename, rename_dbt))
    with pytest.raises(ExportRenderError, match=r"more than one export named") as info:
        render_exports(project)
    assert "dbt parse" not in str(info.value)


def test_duplicate_relations_ignore_schema_case(make_project: MakeProject) -> None:
    # dbt compares relations case-insensitively, schema included.
    def clash(raw: dict[str, Any]) -> None:
        for sq in raw["saved_queries"]:
            if sq["name"] == "items_by_country":
                sq["exports"][0]["config"]["alias"] = "revenue_daily"
                sq["exports"][0]["config"]["schema_name"] = "REPORTING"

    def clash_dbt(raw: dict[str, Any]) -> None:
        export = raw["saved_queries"]["saved_query.fx.items_by_country"]["exports"][0]
        export["unrendered_config"]["schema"] = "REPORTING"

    project = load_project(_mutate(make_project, "core_export", clash, clash_dbt))
    with pytest.raises(ExportRenderError, match=r"alias 'revenue_daily'"):
        render_exports(project)
