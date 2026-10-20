# 08-01 Summary: packaging and walkthrough (the parts that do not need DuckDB)

Phase 7 (DuckDB) waits for duckdb-semantic-views v0.16.0 and a DuckDB 2.0 release of
dbt-duckdb-semantic-views (Anentropic, 2026-10-20). This plan does the Phase 8 work that does
not depend on it; SC3 (DuckDB fixture in the CI matrix) and SC4 (the 1.0.0 tag) come later.

- `dbt-s-layer --version` (test first).
- release.yml: a manual run publishes to TestPyPI by default (input `repository`), a tag push
  to PyPI; the GitHub release is made for tags only; the validate job runs the installed
  `dbt-s-layer --version` and `--help` from the wheel. TestPyPI needs a trusted publisher for
  this repository and workflow, with a `testpypi` environment (maintainer, one-time).
- README "Quick start": install, `dbt parse`, `generate`, `dbt build`.
- CHANGELOG.md (Keep a Changelog) with an Unreleased section.

Checked locally (2026-10-20):
- `uv build` gives a 72 KB wheel and a 61 KB sdist; the wheel installs into clean Python 3.11
  and 3.13 venvs and `dbt-s-layer doctor` runs.
- The wheel co-installs with dbt-core + dbt-duckdb, dbt-core + dbt-snowflake, and dbt-core
  1.12.5 + dbt-databricks 1.12.7 (metricflow 0.213.0, click 8.5.0 in each).
- The quick start on a copy of `export_legacy` (dbt Core 1.12.6, dbt-duckdb, a file database):
  deps, parse, generate (6 files), `dbt build` PASS=11 ERROR=0.
