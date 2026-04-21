# dbt-s-layer

## What This Is

`dbt-s-layer` is a Python package that brings dbt Cloud's semantic layer features to dbt Core and dbt Fusion users. It reads dbt semantic YAML as the single source of truth and code-generates two families of dbt models: warehouse-native semantic/metric views (Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB semantic views) and pre-aggregated export models from `saved_query` exports. A pre-commit hook keeps generated files in sync with YAML changes automatically.

## Core Value

dbt Core users can materialise semantic models and exports directly in the warehouse with no dbt Cloud subscription — semantic YAML remains the single source of truth, generated models are first-class DAG nodes.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Parse dbt semantic manifest via dbt Fusion binary (`dbtf parse` → `semantic_manifest.json`)
- [ ] Generate `sem__*.sql` models for each `semantic_model` as warehouse-native semantic objects (Snowflake SEMANTIC VIEW, Databricks metric view, DuckDB semantic view)
- [ ] Generate `exp__*.sql` models for each `saved_query` export using MetricFlow SQL rendering
- [ ] Rewrite physical table references in generated export SQL to `{{ ref() }}` / `{{ source() }}` via sentinel prefix approach
- [ ] Generate companion `_semantic_views.yml` schema file alongside generated models
- [ ] Pre-commit hook detects changes to semantic YAML files and triggers regeneration
- [ ] CLI entrypoint: `dbt-s-layer generate --project-dir . --output-dir models/semantic/`
- [ ] Works with dbt Core ≥ 1.7 and dbt Fusion (codegen is engine-agnostic)
- [ ] Fast-path for legacy-spec projects (skip Fusion binary, use Python `dir_to_model` parser directly)

### Out of Scope

- YAML translation tool (dbt ↔ Snowflake/Databricks/DuckDB semantic YAML) — future milestone
- dbt Cloud integration — not a target (Cloud has exports natively)
- Incremental exports — MetricFlow has no incremental concept; defer to follow-up
- Contracts and column-doc generation from semantic types — v2 iteration
- `dbt.plugins` / `get_nodes()` API integration — undocumented, Fusion-incompatible; codegen approach preferred

## Context

The project targets two pain points for dbt Core users:

1. **Exports without Cloud.** `saved_query` exports are a dbt Cloud-only feature. MetricFlow's SQL engine is open-source but has no code-generation CLI. This tool closes that gap.

2. **Warehouse-native semantic objects.** Snowflake's `SEMANTIC VIEW` and Databricks metric views declare semantics as first-class warehouse objects, enabling richer BI integrations. Authoring them today requires hand-writing DDL outside the dbt project. This tool derives them from the semantic YAML automatically.

**Architecture decisions (from design notes):**

- **Parsing:** Invoke `dbtf parse` (Fusion binary, ELv2, free for local use) to produce `semantic_manifest.json`. This handles both the legacy dbt 1.x YAML spec and the new dbt 2.x / Core 1.12+ spec. Pure Python `metricflow_semantic_interfaces.parsing.dir_to_model` is only available as a fast-path for legacy-spec projects.
- **SQL generation:** `MetricFlowEngine.explain()` renders export SQL per dialect. A thin `SqlClient` stub satisfies the engine's interface without executing anything.
- **ref() rewriting:** Sentinel prefix approach — rewrite `node_relation.relation_name` to `__REF__.name` / `__SOURCE__.schema.name` before building the engine, then regex-substitute back to Jinja after `explain()`. Depends only on `SqlTable.from_string` (2–3 parts) and `visit_table_node` emitting verbatim output.
- **Semantic view materialisation:** Delegates to `dbt_semantic_view` package (Snowflake-Labs) for Snowflake. Databricks package is in-progress (stub + warning until released). DuckDB uses a project-internal custom materialisation.
- **Codegen over dbt plugin API:** `dbt.plugins.get_nodes()` is undocumented and has no Fusion equivalent. Written-to-disk files work identically on Core and Fusion with no internal API coupling.

**Generated file conventions:**
- `sem__<semantic_model_name>.sql` — semantic/metric view models
- `exp__<saved_query>__<export>.sql` — export models
- Output directory: `models/semantic/` (configurable)
- Upstream `ref()` / `source()` calls are explicit in generated SQL — correct DAG edges visible to `dbt ls`, CI selection, and lineage tools

## Constraints

- **Runtime:** Python package (pip-installable); requires dbt Fusion binary on `PATH` for new-spec projects
- **License:** Must not embed ELv2-licensed code (Fusion crates). Subprocess invocation of the binary is permissible. Apache-2.0 / MIT for this package.
- **dbt compatibility:** dbt Core ≥ 1.7 (semantic models GA); dbt Fusion (any version)
- **Warehouse targets v1:** Snowflake (full, via `dbt_semantic_view`), Databricks (alpha: `dbt-databricks==1.12.0a1`), DuckDB (via `duckdb-semantic-views` extension)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Codegen over `dbt.plugins` API | Fusion has no equivalent plugin hook; codegen works on both engines | — Pending |
| Fusion binary + Python metricflow over pure Python parser | Only approach that handles both old and new YAML specs; no ELv2 contamination | — Pending |
| Sentinel prefix for ref() rewriting | Depends on only two narrow public MetricFlow surfaces; alternatives require subclassing frozen dataclasses or fragile name heuristics | — Pending |
| Generated files committed to repo | Full transparency; readable by any dbt tooling; no runtime state | — Pending |
| Package name: `dbt-s-layer` | Matches repo name | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-19 after initialization*
