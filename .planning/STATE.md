---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 3 complete; Phase 4 next
last_updated: "2026-10-19T15:00:00.000Z"
last_activity: 2026-10-19 -- Phase 3 researched, executed and verified
progress:
  total_phases: 9
  completed_phases: 4
  total_plans: 14
  completed_plans: 14
  percent: 44
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-19)

**Core value:** dbt Core users can materialise semantic models and exports directly in the warehouse with no dbt Cloud subscription — semantic YAML remains the single source of truth, generated models are first-class DAG nodes.
**Current focus:** Phase 4 — Snowflake Adapter

## Current Position

Phase: 4 of 8 (Snowflake Adapter)
Plan: — of — in current phase
Status: Ready to research (HIGH RISK)
Last activity: 2026-10-19 -- Phase 3 executed (3 plans) and verified

Progress: [████░░░░░░] 4 of 9 phases (14 of 14 planned plans)

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

- Phase 4 (Snowflake Adapter) flagged HIGH risk — `/gsd-research-phase` required before planning to verify Snowflake `CREATE SEMANTIC VIEW` grammar and `dbt_semantic_view` package schema
- Minimum supported Fusion version: only 2.0.8 tested; documented as tested, older 2.x not hard-failed (01-RESEARCH.md)
- Verified 2026-10-19: Fusion 2.0.8 and dbt Core 1.12.6 manifests (both specs) load via `parse_manifest_from_dbt_generated_manifest` and render through `explain()` (01-RESEARCH.md)
- dbt Core 1.12.6 silently drops semantic models attached to versioned models; `check_artifacts` warns on it (01-04), as it does for Fusion ignoring legacy-spec semantic models
- Phase 4 adds `sem__*` models to `output.render_output` (and entries in `_semantic_views.yml`); the writer already owns the `sem__` prefix, and the clash check should cover them too
- dbt's relation-clash check is case-sensitive; dbt-s-layer's is case-insensitive on purpose (03-RESEARCH finding 2)
- Fusion 2.0.8 does not apply a saved query's `export_as` to its exports; dbt-s-layer resolves it from manifest.json (02-RESEARCH.md finding 4)

## Session Continuity

Last session: 2026-10-19
Stopped at: Phase 3 complete; next Phase 4 research
Resume file: None
