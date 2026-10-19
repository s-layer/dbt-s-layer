---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 4 complete; Phase 5 next
last_updated: "2026-10-19T15:00:00.000Z"
last_activity: 2026-10-19 -- Phase 4 researched, planned, executed and verified
progress:
  total_phases: 9
  completed_phases: 5
  total_plans: 18
  completed_plans: 18
  percent: 56
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-19)

**Core value:** dbt Core users can materialise semantic models and exports directly in the warehouse with no dbt Cloud subscription — semantic YAML remains the single source of truth, generated models are first-class DAG nodes.
**Current focus:** Phase 5 — Pre-Commit Hook

## Current Position

Phase: 5 of 8 (Pre-Commit Hook)
Plan: — of — in current phase
Status: Ready to plan
Last activity: 2026-10-19 -- Phase 4 executed (4 plans) and verified

Progress: [██████░░░░] 5 of 9 phases (18 of 18 planned plans)

## Performance Metrics

**Velocity:**

- Total plans completed: 8
- Average duration: — min
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: none
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Codegen over `dbt.plugins` API (Fusion has no equivalent; works on both engines)
- Fusion binary subprocess + Python metricflow (handles both YAML specs without ELv2 contamination)
- Sentinel prefix for ref() rewriting (narrow public MetricFlow surfaces)
- Pin only `metricflow==0.213.0`; load manifests via its bundled `metricflow_semantic_interfaces` (pydantic v1 models), not `dbt-semantic-interfaces`
- Databricks metric views target `dbt-databricks >= 1.12.0` (GA; databricks/dbt-databricks#1106 closed by PR #1285, latest 1.12.7), no longer the 1.12.0a1 alpha

### Pending Todos

See `_notes/` directory for captured todos:

- Define s-layer.yml scope and format
- Support dbt semantic layer exports as warehouse materializations
- Materialise dbt semantic layer as native semantic views

### Blockers/Concerns

- Phase 4 generated Snowflake semantic views compile on dbt Core 1.12.6 and Fusion 2.0.8 but have never run on a real Snowflake account; a smoke run before v1 is the only proof that Snowflake accepts the DDL (key rules, NON ADDITIVE BY with qualified dimensions, percentile functions in metrics)
- Phase 6/7 reuse `semantic_views.SemanticViewSpec` and add a renderer to the dispatch in `render_semantic_views`; until then non-Snowflake adapters warn once and get no `sem__` files
- dbt Core 1.12.6 cannot compile a project whose package holds a new-spec semantic model ("depends on 'semantic_model.fx.stores' which is not in the graph"), with or without dbt-s-layer; the Core compile test uses the legacy-spec twin `snowflake_legacy`
- Fusion 2.0.8 warns dbt0101 on the `exp__items_by_country` export SQL at compile (static analysis of metricflow's join subquery); exports could set `static_analysis='off'` too (not done in Phase 4)
- Minimum supported Fusion version: only 2.0.8 tested; documented as tested, older 2.x not hard-failed (01-RESEARCH.md)
- Verified 2026-10-19: Fusion 2.0.8 and dbt Core 1.12.6 manifests (both specs) load via `parse_manifest_from_dbt_generated_manifest` and render through `explain()` (01-RESEARCH.md)
- dbt Core 1.12.6 silently drops semantic models attached to versioned models; `check_artifacts` warns on it (01-04), as it does for Fusion ignoring legacy-spec semantic models
- dbt's relation-clash check is case-sensitive; dbt-s-layer's is case-insensitive on purpose (03-RESEARCH finding 2)
- Fusion 2.0.8 does not apply a saved query's `export_as` to its exports; dbt-s-layer resolves it from manifest.json (02-RESEARCH.md finding 4)

## Session Continuity

Last session: 2026-10-19
Stopped at: Phase 4 complete; next Phase 5 planning
Resume file: None
