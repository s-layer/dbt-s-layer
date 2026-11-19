---
name: verify
description: Prove a change to dbt-s-layer works end to end. Use before saying a change is done, before committing, and before opening or updating a pull request.
---

# Verify a change

Run from the repository root:

```bash
uv sync --locked --dev
prek run --all-files
DBT_S_LAYER_DUCKDB_EXTENSION=required uv run pytest -m "not engine" > /tmp/unit.log 2>&1; echo "unit rc=$?"
uv run pytest -m engine -rs > /tmp/engine.log 2>&1; echo "engine rc=$?"
```

It passes when every command exits 0. The engine tests need dbt Core and dbt Fusion: set
`DBT_S_LAYER_CORE`, `DBT_S_LAYER_CORE_DATABRICKS` and `DBT_S_LAYER_FUSION` to their
binaries, installed the way the `engines` job in `.github/workflows/quality.yml`
installs them. Without them those tests fail under CI and skip locally; say so if you
could not run them.

Take each command's exit status from the command itself. In a pipeline `$?` is the last
command's status, so `cmd | tail` hides a failure. Redirect long output to a file, then
read the file.

## What this covers

- generation for every warehouse, with snapshot tests
- the generated DuckDB views executed, with the numbers compared against the MetricFlow
  exports
- real `dbt compile` / `dbt build` runs on Core and Fusion (engine tests)

## What it does not cover

- Snowflake and Databricks execution: their SQL is compiled, not run
- older semantic_views releases: CI runs the DuckDB tests on two older extension versions
  too

## When it fails

- Read the first failure and fix its cause. Do not rerun hoping for a green, and do not
  skip, loosen or delete the failing test to get one.
- If the failure is real but outside the change, say so in the PR and in your report
  rather than working around it.
