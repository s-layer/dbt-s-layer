# Stack Verification Spike

This directory contains **committed evidence** that the metricflow-family
version pins in `pyproject.toml` are correct. It is not production code —
it is a small, import-only probe you run locally to confirm that the
versions we depend on still expose the import paths we rely on in later
phases. CI enforces the same surface through `tests/test_stack_surface.py`.

## What is verified

1. **Resolved versions** of `metricflow` and `pydantic`. Since metricflow
   0.213.0 the wheel bundles `metricflow_semantics` and
   `metricflow_semantic_interfaces` (its fork of `dbt-semantic-interfaces`),
   so the `metricflow` pin fixes all three.
2. **Import paths** we depend on in later phases:
   - `metricflow_semantic_interfaces.implementations.semantic_manifest.PydanticSemanticManifest`
   - `metricflow_semantics.sql.sql_table.SqlTable` + `SqlTable.from_string`
   - `metricflow.engine.metricflow_engine.MetricFlowEngine` + `explain`
3. **Pydantic v1 vs v2 shape** — does `PydanticSemanticManifest` expose
   `model_fields` (v2) or `__fields__` (v1)? This cascades to all future
   code that imports from `metricflow_semantic_interfaces`.

## How to run

```bash
uv sync --locked --dev
uv run python spike/verify_stack.py
```

Expected outcome: exit code 0 and each line labeled `OK`.

## When to re-run

- After bumping `metricflow` in `pyproject.toml`
- After Python major-version bumps
- If the "Pydantic v1 vs v2 shape" decision is referenced in a design doc
  that needs fresh evidence

## Committed results

`spike/results-<date>.txt` — captured stdout from the canonical run that
justified the current pins in `pyproject.toml`. Re-running should produce
equivalent output (versions may differ if deps were bumped).
