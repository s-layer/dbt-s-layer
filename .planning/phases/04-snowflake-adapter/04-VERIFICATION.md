# Phase 4 Verification

**Status:** passed (compile-verified; not run on a Snowflake account)
**Date:** 2026-10-19

## End to end

Fresh copies of the Snowflake fixture projects, dbt Fusion 2.0.8 and dbt Core 1.12.6 with
dbt-snowflake 1.12.3, dummy credentials, no network access to Snowflake:

| Step | snowflake_spec (Fusion) | snowflake_legacy (dbt Core) |
|---|---|---|
| `dbt deps` (git: dbt_semantic_view v1.0.6) | exit 0 | exit 0 |
| `dbt parse --target snowflake` | exit 0 | exit 0 |
| `dbt-s-layer generate --project-dir P` | 10 files (5 exports, 4 `sem__`, schema file); WARN for `revenue_cumulative` and `revenue_per_item` | same |
| `generate` again | "up to date" | "up to date" |
| `dbt parse` with the generated models | exit 0 | exit 0 |
| `dbt-s-layer check` | exit 0, up to date | exit 0, up to date |
| `dbt compile --target snowflake` (Core: `--no-populate-cache`) | exit 0 | exit 0 |
| `dbt-s-layer doctor --project-dir P` | artifacts fresh, `dbt_semantic_view: installed` | same |

The compiled `sem__*.sql` hold physical relations (`orders AS ANALYTICS.MARTS.orders`,
`customers AS ANALYTICS.MARTS.customers_v1` on Core, `..._v2` on Fusion) and no Jinja. The
same run is automated in tests/test_snowflake_compile.py (2 passed locally with both engines;
skipped in CI, which installs no engine).

Fusion's compile prints one dbt0101 static-analysis warning, for the export
`exp__items_by_country__exp_items_by_country` (metricflow's join subquery), not for a semantic
view: the `sem__` models set `static_analysis='off'`. Recorded in STATE.md.

## Not verified

- **No Snowflake account.** Nothing here proves Snowflake accepts the DDL. Unverified against
  Snowflake: `NON ADDITIVE BY` with table-qualified dimensions (the docs only show bare names;
  qualified ones avoid ambiguity between tables with the same dimension name),
  `PERCENTILE_CONT/DISC ... WITHIN GROUP` and `APPROX_PERCENTILE` inside metrics, explicit
  parent columns in `REFERENCES`, the `--` header line inside the DDL, and the same dimension
  name on several tables of one view. A smoke run on a real account is the remaining proof.
- Name shadowing is checked against the documented resolution rule, not observed.

## Success criteria

1. A `sem__<name>.sql` per semantic model using the `dbt_semantic_view` materialization:
   test_output.py (`test_semantic_view_files_have_the_header_and_body`), snapshots, end to end.
2. Entities, dimensions and metrics match the semantic YAML: test_semantic_views.py (one test
   per mapping row and per skip reason), test_snowflake.py snapshots for all seven artifact sets.
   The package has no config schema, so "generated config" is the DDL body (04-RESEARCH §2).
3. Every table is a `{{ ref() }}` (package and versioned forms included):
   `test_tables_ref_the_node_they_read`; compiled `depends_on` equals the ref map in
   test_snowflake_compile.py.
4. `dbt compile --target snowflake` succeeds with the generated models: end to end above.

Requirements: SEMVW-01 (Snowflake), SEMVW-02, SEMVW-05 complete. Skipped features are v2
requirements SEMVW-V2-03..09.

## Gates

- `uv run pytest`: 569 passed, 2 skipped (the compile test, without engines on PATH)
- With `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE` set: the 2 compile tests pass
- `prek run --all-files`: clean
