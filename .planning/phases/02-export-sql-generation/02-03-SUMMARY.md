---
phase: 02-export-sql-generation
plan: 03
subsystem: exports
tags: [metricflow, exports]

requires:
  - phase: 02-export-sql-generation
    plan: 02
    provides: "SentinelTable"
provides:
  - "`exports.py`: render_exports, ExportModel, sql_client_for, SUPPORTED_ADAPTERS"
affects: [phase-3]

key-files:
  created:
    - "src/dbt_s_layer/exports.py"
    - "tests/test_exports.py"
  modified:
    - "src/dbt_s_layer/artifacts.py"
    - "README.md"

requirements-completed: [EXPRT-01, EXPRT-03]

completed: 2026-10-19
---

# Phase 02 Plan 03: export renderer Summary

**`render_exports(project)` returns one model body per export: config block plus metricflow SQL with refs.**

## Accomplishments

- Render-only `SqlClient` for eight metricflow dialects, picked from `metadata.adapter_type` or `adapter=`.
- One `MetricFlowEngine` per saved query. Research first read the engine's plan cache as order dependence; the real effect is that an engine renders a query it has already planned with different aliases, so two identical saved queries would differ. The test for it fails with a shared engine.
- Config: `materialized` from export `unrendered_config` > saved query `config` > `table`; `alias` always; `schema` only when set. Identical for both engines despite Fusion's inheritance gap.
- Errors name the saved query or export: invalid query, missing from manifest.json, unknown `export_as`, bind parameters, duplicate file name, and export names that are not `[A-Za-z0-9_]+` (metricflow does not validate export names, and they become file names).
- Oracle test: each body, rendered by jinja2 with `ref()` resolved through manifest.json, equals metricflow's own SQL against the physical tables, including a dimension `expr` containing `{{ x }}` and `{%`.

## Manual check

Generated the five models into a copy of `export_legacy`, then `dbt build` on DuckDB with dbt Core 1.12.6: all built (PASS=11), the `schema` exports landed in `marts_reporting` / `marts_sq_schema`, and `exp_region_country` depends on `model.fx.orders`, `model.fx.customers.v1` and `model.fx_dep.stores`.
