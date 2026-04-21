# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-19)

**Core value:** dbt Core users can materialise semantic models and exports directly in the warehouse with no dbt Cloud subscription — semantic YAML remains the single source of truth, generated models are first-class DAG nodes.
**Current focus:** Phase 0 — Foundation & Governance

## Current Position

Phase: 0 of 8 (Foundation & Governance)
Plan: — of — in current phase
Status: Ready to plan
Last activity: 2026-04-18 — Roadmap created, 25/25 requirements mapped

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
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

### Pending Todos

See `_notes/` directory for captured todos:
- Define s-layer.yml scope and format
- Support dbt semantic layer exports as warehouse materializations
- Materialise dbt semantic layer as native semantic views

### Blockers/Concerns

- Phase 2 (Export SQL Generation) flagged HIGH risk — `/gsd-research-phase` required before planning to verify MetricFlow internals against pinned versions
- Phase 4 (Snowflake Adapter) flagged HIGH risk — `/gsd-research-phase` required before planning to verify Snowflake `CREATE SEMANTIC VIEW` grammar and `dbt_semantic_view` package schema
- Metricflow Pydantic version (v1 vs v2) unresolved — Phase 0 stack verification spike must settle this
- Fusion GA status + minimum supported version unresolved — Phase 0 spike

## Session Continuity

Last session: 2026-04-18
Stopped at: ROADMAP.md + STATE.md written; ready for `/gsd-plan-phase 0`
Resume file: None
