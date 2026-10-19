# Phase 2: Export SQL Generation - Context

**Gathered:** 2026-10-19
**Status:** Ready for planning
**Source:** ROADMAP Phase 2, REQUIREMENTS EXPRT-01..05, hands-on research (02-RESEARCH.md)

<domain>
## Phase Boundary

Given a loaded `SemanticProject`, render one dbt model body per saved-query export: a
`{{ config() }}` block plus metricflow SQL whose table references are dbt `ref()` calls.
Output is in memory (`ExportModel` objects); Phase 3 writes the files.

**In scope:**
- Export fixtures from both engines, including a local package and versioned models
- Sentinel table: token per semantic-model node and per time spine, pre-flight, rewrite, post-flight, Jinja escaping (EXPRT-02, 04, 05)
- Export renderer: dialect from `metadata.adapter_type`, stub `SqlClient`, one engine per saved query, config block (EXPRT-01, 03)

**Out of scope:**
- Writing files, header comments, model-name collisions with user models (Phase 3)
- Incremental exports, contracts (v2)
</domain>

<decisions>
## Implementation Decisions

- Refs are built from the node (`RefTarget.package/model_name/version`), not copied from the user's expression; the root package is never named, versions are always pinned.
- `__SOURCE__` is reserved but never emitted: semantic models cannot read sources (02-RESEARCH finding 2). Success criterion 2 is met for the three forms dbt can produce; the fourth is covered by pre-/post-flight.
- A time spine that maps to no node does not fail the run; only an export whose SQL uses it fails, naming the relation.
- One `MetricFlowEngine` per saved query (02-RESEARCH finding 1).
- Materialisation: export `unrendered_config.export_as` > saved query `config.export_as` > `table`; `schema` only when the user set it; `alias` always (the engines default it to the export name).
- Supported dialects: the metricflow renderers for `snowflake`, `duckdb`, `databricks`, `bigquery`, `postgres`, `redshift`, `trino`, `athena`. Anything else is an error that lists them. `render_exports(..., adapter=...)` overrides `adapter_type` (Phase 3's `--adapter`).
- Escape `{{`, `{%`, `{#` in metricflow's text as `{{ '{{' }}` etc., so dbt renders the SQL back to exactly what metricflow wrote.
- File name `exp__<saved_query>__<export>.sql`; two exports mapping to one name is an error.
</decisions>
