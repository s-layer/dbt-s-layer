# Requirements: dbt-s-layer

**Defined:** 2026-04-19
**Core Value:** dbt Core users can materialise semantic models and exports directly in the warehouse with no dbt Cloud subscription — semantic YAML remains the single source of truth, generated models are first-class DAG nodes.

## v1 Requirements

### Parsing

- [x] **PARSE-01**: Tool reads `target/semantic_manifest.json` produced by the user running `dbt parse` or `dbtf parse` — the tool does not invoke the dbt parser itself
- [x] **PARSE-02**: Tool works with `semantic_manifest.json` from both dbt Core (legacy YAML spec) and dbt Fusion (new 2.x spec) without any per-spec configuration, since both parsers produce the same artifact format
- [x] **PARSE-03**: Tool resolves each semantic model's upstream dependencies against `manifest.json` `depends_on.nodes` to build the ref/source sentinel map

### Export Codegen

- [x] **EXPRT-01**: Tool generates one `exp__<saved_query>__<export>.sql` dbt model for each `export` block within a `saved_query`, using MetricFlow's SQL rendering engine
- [x] **EXPRT-02**: Generated export SQL contains `{{ ref('...') }}`, `{{ source('...', '...') }}`, `{{ ref('project', 'model') }}` (cross-project), and `{{ ref('model', v=N) }}` (versioned) Jinja calls in place of physical table references (sentinel-prefix rewrite approach). `source()` is never needed: dbt does not let a semantic model read a source
- [x] **EXPRT-03**: Generated export model file contains a `{{ config() }}` block honouring the `export.config` materialisation strategy (`table`, `view`) from the YAML
- [x] **EXPRT-04**: Tool runs a pre-flight check that no dbt model or source name collides with the reserved sentinel prefixes (`__REF__`, `__SOURCE__`, `__XREF__`, `__REFV__`) and errors with a clear message if a collision is found. Implemented over what can reach generated SQL: every semantic manifest string and the name/alias of each model a semantic model reads. Source names cannot reach it (semantic models cannot read sources)
- [x] **EXPRT-05**: Tool runs a post-flight check that no generated file contains an unresolved sentinel token, and refuses to write any such file

### Semantic View Codegen

- [x] **SEMVW-01**: Tool generates one `sem__<semantic_model_name>.sql` dbt model for each `semantic_model`, containing the DDL or materialisation call for the target warehouse's native semantic object type
- [x] **SEMVW-02**: Generated semantic view models for Snowflake target use the `dbt_semantic_view` package (Snowflake-Labs) materialisation, with entities, dimensions, and measures derived from the semantic model YAML
- [ ] **SEMVW-03**: Generated semantic view models for Databricks target use the `dbt-databricks` metric view materialisation (`dbt-databricks >= 1.12.0` (GA), PR databricks/dbt-databricks#1285), with entities, dimensions, and measures derived from the semantic model YAML
- [ ] **SEMVW-04**: Generated semantic view models for DuckDB target use the `duckdb-semantic-views` extension (https://anentropic.github.io/duckdb-semantic-views/), delegating to its materialisation in the same pattern as the Snowflake adapter
- [x] **SEMVW-05**: Generated semantic view models include explicit `{{ ref() }}` and `{{ source() }}` calls for all upstream dbt nodes, so dbt's DAG parser registers correct lineage edges

### File Output

- [x] **OUTPT-01**: Tool generates a `_semantic_views.yml` companion schema file alongside generated `sem__*.sql` and `exp__*.sql` models
- [x] **OUTPT-02**: Generated output is deterministic and byte-stable — identical YAML inputs produce byte-identical output files across runs (sorted keys, stable ordering, no embedded timestamps)
- [x] **OUTPT-03**: File writes are atomic — all files for a run are written to a staging directory first, then moved into place with `os.replace`, so a partial run never leaves the output directory in an inconsistent state
- [x] **OUTPT-04**: Output directory is configurable (default: `models/semantic/`); generated files are prefixed `sem__` (semantic view models) and `exp__` (export models)
- [x] **OUTPT-05**: Generated files include a header comment marking them as auto-generated and identifying the source semantic model or saved query, to prevent accidental hand-editing

### CLI

- [x] **CLI-01**: `dbt-s-layer generate` command runs the full codegen pipeline for a dbt project directory
- [x] **CLI-02**: `dbt-s-layer check` command re-runs codegen into a temp directory and exits non-zero if the output differs from committed files — suitable for CI enforcement
- [x] **CLI-03**: `dbt-s-layer doctor` command validates the environment: `semantic_manifest.json` presence and freshness, supported metricflow version, and output directory writability
- [x] **CLI-04**: `dbt-s-layer generate` accepts `--project-dir`, `--output-dir`, `--target` (dbt target name), and `--adapter` (warehouse override) flags
- [x] **CLI-05**: All commands produce actionable error messages when `semantic_manifest.json` is missing or older than the most recently modified semantic YAML file, prompting the user to run `dbt parse` or `dbtf parse`

### Pre-commit Hook

- [x] **HOOK-01**: Package ships a `.pre-commit-hooks.yaml` defining a `dbt-s-layer` hook that runs `dbt-s-layer generate` on changes to files containing `semantic_models:`, `metrics:`, or `saved_queries:` YAML keys
- [x] **HOOK-02**: Pre-commit hook completes within 5 seconds with dbt Fusion on a project with up to 100 semantic models and 50 exports; with dbt Core the hook is bounded by Core's own `dbt parse` time (documented in README)

## v2 Requirements

### Export Enhancements

- **EXPRT-V2-01**: Incremental export materialisation — auto-generate `{% if is_incremental() %}` predicates for exports with a primary time dimension
- **EXPRT-V2-02**: Auto-generate dbt contracts and column documentation in the `_semantic_views.yml` schema file, sourcing types and descriptions from semantic model dimension/measure definitions

### Semantic View Enhancements

- **SEMVW-V2-01**: ~~Full Databricks metric view support once upstream dbt package is GA~~ Withdrawn: metric views shipped GA in dbt-databricks 1.12.0, so full support is v1 scope (SEMVW-03)
- **SEMVW-V2-02**: Adapter expansion beyond Snowflake/Databricks/DuckDB (BigQuery, Redshift, Postgres) — pull-driven based on user demand

Semantic model features v1 leaves out of semantic views (each is skipped with a warning; Phase 4):

- **SEMVW-V2-03**: Cumulative metrics in semantic views (Snowflake window metrics partition by the queried dimensions, not a time spine; exports cover them today)
- **SEMVW-V2-04**: Conversion metrics in semantic views
- **SEMVW-V2-05**: Metric filters (on a metric or its input) rendered as `CASE WHEN <filter> THEN <expr> END` inside the aggregate, with `{{ Dimension(...) }}` resolved against the view's columns
- **SEMVW-V2-06**: Non-additive dimensions with `window_groupings` (MetricFlow takes the last rows per entity, Snowflake per queried dimensions)
- **SEMVW-V2-07**: Natural entities and validity windows (SCD type 2), e.g. through `ASOF` relationships
- **SEMVW-V2-08**: Derived and ratio metrics with offsets, filters, inputs from more than one semantic model, or derived inputs; and more than one join path to a table (Snowflake `USING`, a preview feature)
- **SEMVW-V2-09**: `fill_nulls_with` / `join_to_timespine` on metrics (ignored today) and approximate percentiles the warehouse has no function for (discrete on Snowflake, continuous on Databricks; skipped today)
- **SEMVW-V2-10**: Databricks dimensions of a joined semantic model whose `expr` is not a plain column (needs the join's `source` to be a SQL query that computes them; skipped today)

### Tooling

- **TOOL-V2-01**: YAML format translation CLI — bidirectional conversion between dbt semantic YAML (legacy 1.x and new 2.x), Snowflake semantic view YAML, Databricks metric view YAML, and duckdb-semantic-views format
- **TOOL-V2-02**: Diff-based / selective regeneration — only regenerate models affected by a YAML change rather than full project regeneration
- **TOOL-V2-03**: Description propagation from semantic YAML into SQL comments and dbt model descriptions

## Out of Scope

| Feature | Reason |
|---------|--------|
| Query execution / metric query API | That's `dbt-metricflow`; explicit anti-feature — don't duplicate |
| dbt Cloud integration | dbt Cloud has exports natively; not a target audience |
| Custom semantic YAML dialect / `s-layer.yml` authoring format | Would fork users' source of truth; dbt semantic YAML is the single source of truth |
| GUI / BI config generators | Out of scope for a developer CLI tool |
| `dbt.plugins.get_nodes()` integration | Undocumented; Fusion-incompatible; codegen approach is preferred |
| PyO3 bindings to Fusion crates | ELv2 license contamination; no Python bindings exist upstream |
| `[tool.dbt-s-layer]` pyproject.toml config section | CLI flags are sufficient for v1; add if user demand emerges |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PARSE-01 | Phase 1 | Complete |
| PARSE-02 | Phase 1 | Complete |
| PARSE-03 | Phase 1 | Complete |
| EXPRT-01 | Phase 2 | Complete |
| EXPRT-02 | Phase 2 | Complete |
| EXPRT-03 | Phase 2 | Complete |
| EXPRT-04 | Phase 2 | Complete |
| EXPRT-05 | Phase 2 | Complete |
| SEMVW-01 | Phase 4 | Complete (Snowflake; Databricks and DuckDB in Phases 6, 7) |
| SEMVW-02 | Phase 4 | Complete |
| SEMVW-03 | Phase 6 | Implemented; real-workspace smoke test pending |
| SEMVW-04 | Phase 7 | Pending |
| SEMVW-05 | Phase 4 | Complete |
| OUTPT-01 | Phase 3 | Complete |
| OUTPT-02 | Phase 3 | Complete |
| OUTPT-03 | Phase 3 | Complete |
| OUTPT-04 | Phase 3 | Complete |
| OUTPT-05 | Phase 3 | Complete |
| CLI-01 | Phase 3 | Complete |
| CLI-02 | Phase 3 | Complete |
| CLI-03 | Phase 0 | Complete (output directory check in Phase 3) |
| CLI-04 | Phase 3 | Complete |
| CLI-05 | Phase 1 | Complete (`check_artifacts`; Phase 3 `generate`/`check` must call it) |
| HOOK-01 | Phase 5 | Complete |
| HOOK-02 | Phase 5 | Complete |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 25
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-19*
*Last updated: 2026-10-19 after Phase 4*
