# Phase 2: Export SQL Generation - Research

**Researched:** 2026-10-19, hands-on against metricflow 0.213.0, dbt Core 1.12.6 and Fusion 2.0.8
**Confidence:** HIGH for everything marked "verified" (run locally, outputs inspected)

## Summary

The sentinel approach works as designed. With every semantic model's and time spine's
`node_relation.relation_name` replaced by a two-part token such as `__REF__.n0_orders`,
`MetricFlowEngine.explain()` renders the token verbatim in `FROM` / `JOIN` clauses for all
seven metricflow dialects we tried (Snowflake, DuckDB, Databricks, BigQuery, Postgres,
Redshift, Trino), for simple, cumulative (time spine join), multi-hop join, package-owned and
versioned semantic models. The SQL needs no warehouse: a stub `SqlClient` that exposes only
`sql_engine_type` and `sql_plan_renderer` is enough.

Five findings change the original design:

1. **An engine renders a repeated query differently** (verified). `MetricFlowEngine` caches
   plans: asked for the same query a second time it renders different subquery aliases
   (`subq_8` instead of `subq_11`). Two saved queries with the same query params would then get
   different SQL depending on which was rendered first. A fresh engine per saved query (sharing
   one `SemanticManifestLookup`) is byte-stable across runs, orders and processes. Engine
   construction costs ~30 ms. **Use one engine per saved query.**
2. **Semantic models cannot read sources** (verified). dbt Core rejects
   `model: source('raw', 'events')` with "'source' is undefined", so `__SOURCE__` is never
   produced. It stays reserved (pre-flight still rejects it) but there is nothing to rewrite.
3. **Time spines need their own mapping** (verified). They have no `ref()` expression and no
   `depends_on`. Both `time_spines[*].node_relation.relation_name` and the legacy
   `time_spine_table_configurations[*].location` (dbt Core writes the latter when the project
   has a `metricflow_time_spine` model but no `time_spine:` YAML) equal the `relation_name` of
   the node in manifest.json, in the same engine's quoting. Map by that string.
4. **Fusion does not inherit saved-query-level `export_as`** (verified). With
   `config: {export_as: view}` on the saved query and none on the export, dbt Core writes
   `export_as: view` into the export and Fusion writes `table`. manifest.json keeps what the
   user wrote (`saved_queries[*].config`, `exports[*].unrendered_config`) in both engines, so we
   resolve materialisation from there: export `unrendered_config` > saved query `config` >
   `table`.
5. **dbt Jinja-renders semantic YAML expressions** (verified). A dimension or measure `expr`
   containing `{%` fails `dbt parse`; users must write `{{ '{%' }}`, which reaches the manifest
   as a literal `{%`. Generated SQL is a dbt model, so dbt renders it again: every `{{`, `{%`,
   `{#` in metricflow's output must be escaped or dbt will fail or, worse, run it.

## Ref construction

Use the node, not the user's expression. The user's `ref()` is written relative to the package
that declares the semantic model: Fusion records `ref('stores')` for a semantic model in the
local package `fx_dep`, and `ref('customers')` (unversioned, resolves to latest v2) for the
versioned one. Inside the root project those are ambiguous or unpinned. Built from
`RefTarget` (Phase 1):

| Node | Sentinel prefix | Jinja |
|------|-----------------|-------|
| root package, unversioned | `__REF__` | `{{ ref('orders') }}` |
| other package/project, unversioned | `__XREF__` | `{{ ref('fx_dep', 'stores') }}` |
| versioned (any package) | `__REFV__` | `{{ ref('customers', v=1) }}` / `{{ ref('pkg', 'm', v=2) }}` |
| (never produced) | `__SOURCE__` | — |

Pinning the version is deliberate: the SQL was rendered against that version's relation. When
`latest_version` moves, the artifacts change and the export is regenerated (Phase 3 `check`).

## Sentinel token shape

`SqlTable.from_string` splits on `.` and accepts 2 or 3 parts. Tokens are
`<PREFIX>.n<index>_<name with non-word chars replaced by _>`: always two `\w+` parts, unique by
index, readable in error messages. The rewrite is a lookup in an explicit token table, never a
reconstruction from the token text.

## Collision checks

- Pre-flight (EXPRT-04): any string in semantic_manifest.json (names, `expr`s, filters, relation
  names) or any name/alias of a node we emit a `ref()` for that contains a reserved prefix
  (case-insensitive) aborts with the location. Those are the only inputs that can reach the
  output. Source names cannot reach it (finding 2), so sources are not scanned.
- Post-flight (EXPRT-05): after rewriting, any reserved prefix left in the file body
  (case-insensitive) refuses the file. So does a non-empty `bind_parameter_set` (not seen in
  any test query, but the SQL would be incomplete).

## Export config

`{{ config(materialized='table'|'view', alias='<export alias>') }}`, plus `schema='<x>'` only
when the user set a schema (export `unrendered_config.schema`, else saved query
`config.schema`). The engines always fill `schema_name` with the target schema, so writing it
unconditionally would hard-code the profile's schema. An explicit schema goes through the
project's `generate_schema_name`, as for any dbt model (dbt Cloud writes it verbatim); README
must say so.

## Fixture projects

Two new projects, both with a local package `fx_dep` (model + semantic model `stores`) installed
by `dbt deps`:

- `export_spec` (new spec, parsed by Fusion with the `snowflake` target): orders, order_items
  (alias `Order_Items`), versioned customers (v1, v2, latest 2), cumulative metric, saved queries
  `revenue_by_day` (table + view export with alias and schema), `revenue_by_region_country`
  (join to package model and versioned model, `where` filter), `items_by_country` (multi-hop,
  saved-query-level `export_as: view` + `schema`), `cumulative_revenue` (time spine).
- `export_legacy` (legacy spec, parsed by dbt Core with the `duckdb` target): the same queries,
  with `customers` pinned as `ref('customers', v=1)` (not the latest version). dbt Core drops
  new-spec semantic models on versioned models, so this is the only Core route to `__REFV__`.

`dbt_packages/` is not committed; `regen_fixtures.py` runs `dbt deps` first when a project has
`packages.yml`.

## Out of scope / deferred

- Generated model names colliding with user models, and the output directory: Phase 3
  (OUTPT, CLI-03).
- File header comment: Phase 3 (OUTPT-04).
