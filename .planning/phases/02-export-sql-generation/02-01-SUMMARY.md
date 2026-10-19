---
phase: 02-export-sql-generation
plan: 01
subsystem: fixtures
tags: [fixtures, exports]

provides:
  - "Export artifact sets fusion_export (Fusion 2.0.8, export_spec) and core_export (dbt Core 1.12.6, export_legacy)"
affects: [phase-2, phase-3]

key-files:
  created:
    - "tests/fixtures/projects/export_spec/**"
    - "tests/fixtures/projects/export_legacy/**"
    - "tests/fixtures/artifacts/{fusion_export,core_export}/**"
  modified:
    - "scripts/regen_fixtures.py"
    - "tests/conftest.py"
    - "tests/test_fixtures.py"
    - "tests/test_refs.py"

requirements-completed: []

completed: 2026-10-19
---

# Phase 02 Plan 01: export fixtures Summary

**Two real-engine artifact sets with a local package, versioned models, a time spine join, a multi-hop join and every export config case.**

## Accomplishments

- `export_spec` (new spec, Fusion, snowflake target) and `export_legacy` (legacy spec, dbt Core, duckdb target), each with the local package `fx_dep` providing the `stores` semantic model.
- dbt Core reaches a versioned model only through the legacy spec; `export_legacy` pins `ref('customers', v=1)` (not the latest), Fusion follows latest (v2).
- `regen_fixtures.py` runs `dbt deps` first when a project has `packages.yml`; two consecutive runs give no diff.
- Corpus guards pin the Fusion export_as inheritance difference so a Fusion fix flips a test.
- `make_project` ignores `target/`, `logs/`, `dbt_packages/` left by local runs.

## Deviations

- A metric with a Jinja-escaped `expr` was dropped from the fixtures: Fusion rejects it ("did not match any variant of untagged enum MetricExpr"). Escaping is covered by mutating a loaded manifest in tests instead.
