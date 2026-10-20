# 06-01 Summary: Databricks metric views

- `databricks.render_databricks(spec) -> RenderedView` writes the body: the `#` ownership
  header (added by `output`), `{{ config(materialized='metric_view', static_analysis='off') }}`,
  a blank line, then hand-rendered YAML (`version: 1.1`, `comment`, `source`, nested `joins`,
  `dimensions`, `measures`) with double-quoted scalars. `yaml_string` escapes `\ " \n \r \t`,
  other control characters as `\xNN`, and `$` as `\x24`, so no expr can close dbt-databricks'
  `$$` wrapper; every body goes through `escape_jinja` and `postflight`.
- IR: `RelationshipSpec.entity` names joined fields (`order__customer__country`), `label`
  becomes `display_name`, `build_semantic_views(adapter=)` skips the approximate percentile the
  adapter lacks (discrete on Snowflake, continuous on Databricks) with a warning.
- Renderers return `RenderedView(body, warnings)`; `render_semantic_views` dispatches on
  `snowflake` and `databricks` and warns when manifest.json has no
  `macro.dbt_databricks.materialization_metric_view_databricks`; `doctor --project-dir`
  reports it. `output._is_generated` accepts the `--` and `#` marks.
- A private metric is emitted only when a derived or ratio metric uses it (with a warning). A
  joined table's non-bare expr is skipped with a warning (SEMVW-V2-10). A semantic model named
  `source`, two names that fold to the same string, or a field that shadows a column another
  expr uses skip the view with a warning.
- Fixtures: `databricks_spec` (Fusion 2.0.8) and `databricks_legacy` (dbt Core 1.12.5 +
  dbt-databricks 1.12.7, which needs dbt-core < 1.12.6; adds an approximate discrete
  percentile). `scripts/regen_fixtures.py --core-databricks PATH --sets ...`.
- Snapshots of every artifact set rendered for Databricks under
  `tests/snapshots/semantic_views/databricks/`, each checked to load as YAML once refs render.
- `test_databricks_compile.py` runs parse, generate, parse, check and compile on both engines
  with no workspace (Core with `--no-populate-cache`) and checks materialization, depends_on and
  that each compiled definition is YAML. Passed locally on both; CI skips it (no engines).
- The engine helpers of the compile and hook-scale tests moved to `tests/engines.py`.
- Commits, tests red first: 996713c then 6a70f83 (IR), 3a287d2 and c1dd6b5 then 50d970e
  (renderer and wiring), 3e49f24 (fixtures, snapshots, compile test).
- `uv run pytest`: 858 passed with both engines available; `prek run --all-files` clean.
