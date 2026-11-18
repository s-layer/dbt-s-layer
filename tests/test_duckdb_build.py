"""
End to end on a real dbt engine: generated semantic views build and answer on DuckDB.

Copies a DuckDB fixture project (`duckdb_spec` for Fusion, `duckdb_legacy` for dbt
Core, as in test_snowflake_compile.py), installs its packages (the
duckdb_semantic_views materialization, from GitHub), and runs `dbt parse`,
`dbt-s-layer generate`, `dbt parse` again, `dbt-s-layer check` and `dbt build`
into a DuckDB file. The project's on-run-start hooks install and load the
semantic_views extension, and a data test added after `generate` queries a view.

Members with an `s_layer` block get `WITH TAG` when the loaded extension is 0.17 or
later (`s_layer_duckdb_tags` is left unset, so the models decide); dbt Fusion's
DuckDB 1.5.4 gets 0.11. With DBT_S_LAYER_DUCKDB_TAGS=required, dbt Core's build also
checks the tags of `revenue` through DESCRIBE.

Engines come from `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE` (paths), else from
PATH (`dbtf` or a Fusion `dbt`; a dbt Core `dbt` with the duckdb adapter). Each
engine's test skips when it is not found, and when `dbt deps` cannot download the
package. CI installs both engines in its `engines` job, where a skip fails the run
(tests/ci_skips.py).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from dbt_s_layer.cli import app
from dbt_s_layer.project import load_project
from dbt_s_layer.refs import build_ref_map
from dbt_s_layer.semantic_views import build_semantic_views
from tests.engines import copy_project, find_engine, ok, run

if TYPE_CHECKING:
    from pathlib import Path

# Fails (returns rows) unless sem__orders gives the fixture's numbers, worked out by hand (see
# test_duckdb_execute.test_the_numbers). A NULL or a missing or extra row counts as a mismatch.
ANSWER_TEST = """\
{{ config(static_analysis='off') }}
{%- set view = ref('sem__orders') %}
with actual as (
    select * from semantic_view(
        '{{ view.schema }}.{{ view.identifier }}',
        dimensions := ['customer__country', 'order_month'],
        metrics := ['revenue', 'paid_revenue', 'average_order_value']
    )
),
expected (customer__country, order_month, revenue, paid_revenue, average_order_value) as (
    values
        ('fr', date '2024-01-01', 25.00, 25.00, 25.00),
        ('fr', date '2024-02-01', 12.50, 12.50, 12.50),
        ('gb', date '2024-01-01', 17.50, 10.00, 8.75),
        ('gb', date '2024-02-01', 40.00, 40.00, 40.00),
        (null, date '2024-02-01', 5.00, 5.00, 5.00)
)
select *
from expected e
full outer join actual a
    on a.customer__country is not distinct from e.customer__country
    and cast(a.order_month as date) = e.order_month
where a.order_month is null or e.order_month is null
    or a.revenue is distinct from e.revenue
    or a.paid_revenue is distinct from e.paid_revenue
    or a.average_order_value is distinct from e.average_order_value
"""

# Fails (returns rows) unless two exports, built as tables by MetricFlow's SQL, hold what the
# semantic view answers for the same saved query. The paid-only filter is applied by grouping
# by status too, since dbt Fusion's extension (0.11) has no where_clause.
EXPORTS_TEST = """\
{{ config(static_analysis='off') }}
{%- set view = ref('sem__orders') %}
{%- set name = view.schema ~ '.' ~ view.identifier %}
with by_day as (
    select ordered_at, revenue from semantic_view(
        '{{ name }}', dimensions := ['ordered_at'], metrics := ['revenue']
    )
),
paid as (
    select customer__country, store__region, sum(revenue) as revenue
    from semantic_view(
        '{{ name }}',
        dimensions := ['customer__country', 'store__region', 'status'],
        metrics := ['revenue']
    )
    where status = 'paid'
    group by customer__country, store__region
)
select 'exp_revenue_by_day' as export, count(*) as mismatches
from {{ ref('exp__revenue_by_day__exp_revenue_by_day') }} x
full outer join by_day v on cast(x.order__ordered_at__day as date) = v.ordered_at
where x.order__ordered_at__day is null or v.ordered_at is null
    or x.revenue is distinct from v.revenue
having count(*) > 0
union all
select 'exp_region_country', count(*)
from {{ ref('exp__revenue_by_region_country__exp_region_country') }} x
full outer join paid v
    on x.customer__country is not distinct from v.customer__country
    and x.store__region is not distinct from v.store__region
where x.revenue is null or v.revenue is null or x.revenue is distinct from v.revenue
having count(*) > 0
"""

# Fails (returns a row) unless sem__orders' revenue carries its S_LAYER_KIND tag.
TAGS_TEST = """\
{{ config(static_analysis='off') }}
{%- set view = ref('sem__orders') %}
select 1 where (
    select count(*) from describe_semantic_view('{{ view.schema }}.{{ view.identifier }}')
    where object_name = 'revenue' and property = 'TAGS'
        and json_extract_string(property_value, '$.S_LAYER_KIND') = 'currency'
) <> 1
"""

pytestmark = pytest.mark.engine


@pytest.mark.parametrize(
    ("kind", "project"), [("Fusion", "duckdb_spec"), ("dbt Core", "duckdb_legacy")]
)
def test_generated_semantic_views_build(kind: str, project: str, tmp_path: Path) -> None:
    variable = "DBT_S_LAYER_FUSION" if kind == "Fusion" else "DBT_S_LAYER_CORE"
    binary = find_engine(kind, variable, "duckdb")
    if binary is None:
        pytest.skip(f"no {kind} engine (set {variable})")
    project_dir = copy_project(project, tmp_path)
    deps = run(binary, "deps", cwd=project_dir)
    if deps.returncode != 0:
        pytest.skip(f"`dbt deps` could not install duckdb_semantic_views: {deps.stdout[-500:]}")

    ok(run(binary, "parse", cwd=project_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["generate", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "duckdb_semantic_views package is not installed" not in result.output
    semantic_dir = project_dir / "models" / "semantic"
    generated = sorted(p.name for p in semantic_dir.glob("sem__*.sql"))
    assert generated == [
        "sem__customers.sql",
        "sem__order_items.sql",
        "sem__orders.sql",
        "sem__stores.sql",
    ]
    tests_dir = project_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "sem_orders_answers.sql").write_text(ANSWER_TEST)
    (tests_dir / "sem_orders_match_exports.sql").write_text(EXPORTS_TEST)
    tags = kind == "dbt Core" and os.environ.get("DBT_S_LAYER_DUCKDB_TAGS") == "required"
    if tags:
        (tests_dir / "sem_orders_tags.sql").write_text(TAGS_TEST)

    ok(run(binary, "parse", cwd=project_dir))
    result = runner.invoke(app, ["check", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output

    built = run(binary, "build", cwd=project_dir)
    ok(built)
    assert "sem_orders_answers" in built.stdout
    assert "sem_orders_match_exports" in built.stdout
    assert ("sem_orders_tags" in built.stdout) == tags

    loaded = load_project(project_dir)
    refs = build_ref_map(loaded)
    manifest: dict[str, Any] = json.loads((project_dir / "target" / "manifest.json").read_text())
    nodes = {n["name"]: n for n in manifest["nodes"].values() if n["name"].startswith("sem__")}
    assert sorted(f"{name}.sql" for name in nodes) == generated
    for view in build_semantic_views(loaded, adapter="duckdb").views:
        node = nodes[f"sem__{view.name}"]
        assert node["config"]["materialized"] == "semantic_view"
        expected = sorted(refs[t.semantic_model].node_id for t in view.tables)
        assert sorted(node["depends_on"]["nodes"]) == expected
