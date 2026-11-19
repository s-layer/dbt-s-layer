---
name: verify
description: Prove a change to dbt-s-layer works end to end. Use before saying a change is done, before committing, and before opening or updating a pull request.
---

# Verify a change

Run from the repository root:

```bash
export CI=true  # CI's skip gate: a skipped test fails the run
uv sync --locked --dev
uv tool install prek  # once; skip if prek is already on PATH
prek run --all-files
DBT_S_LAYER_DUCKDB_EXTENSION=required uv run pytest -m "not engine" > /tmp/unit.log 2>&1; echo "unit rc=$?"
uv run pytest -m engine -rs > /tmp/engine.log 2>&1; echo "engine rc=$?"
```

It passes when every command exits 0 and every printed `rc=` is 0. The engine tests need
dbt Core and dbt Fusion. `tests/engines.py` uses `DBT_S_LAYER_CORE`,
`DBT_S_LAYER_CORE_DATABRICKS` and `DBT_S_LAYER_FUSION` when they are set, and otherwise
whatever `dbt` or `dbtf` is first on PATH, which may not be the version CI pins. Install
them the way the `engines` job in `.github/workflows/quality.yml` does and point the
variables at them. With `CI=true` a missing engine fails the run instead of skipping
every test; if you could not run them, say so.

`CI=true` turns on `tests/ci_skips.py`, which fails the run on any skip not allowed
there, as CI does. Without it a skipped test only shows in the summary, and a run that
skipped what you changed is not a pass.

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
- Python versions other than the one you ran: CI runs a version matrix

## When it fails

- Read the first failure and fix its cause. Do not rerun hoping for a green, and do not
  skip, loosen or delete the failing test to get one.
- If the failure is real but outside the change, say so in the PR and in your report
  rather than working around it.
