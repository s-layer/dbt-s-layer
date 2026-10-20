# Roadmap: dbt-s-layer

## Overview

dbt-s-layer is a Python codegen tool that reads dbt semantic YAML (via `semantic_manifest.json`) and emits two families of dbt models: warehouse-native semantic view models (`sem__*.sql`) and pre-aggregated export models (`exp__*.sql`). The journey starts with foundation and governance (Phase 0), builds the parsing pipeline that ingests the semantic manifest (Phase 1), then tackles the highest-risk unit early — MetricFlow-driven export SQL generation with sentinel-prefix `ref()` rewriting (Phase 2). Phase 3 delivers deterministic file output plus the `generate` / `check` / `doctor` CLI. With this foundation in place, Phase 4 wires up the primary v1 target (Snowflake semantic views via `dbt_semantic_view`). Phase 5 ships the pre-commit hook that keeps generated files in sync. Phases 6 and 7 add Databricks (metric view via `dbt-databricks` >= 1.12.0, GA) and DuckDB (custom materialisation) adapters. Phase 8 hardens the package for v1 release.

**Risk callout:** Phase 2 (MetricFlow internals + sentinel rewrite) and Phase 4 (Snowflake `SEMANTIC VIEW` DDL + `dbt_semantic_view` package integration) are flagged HIGH risk by research. Both phases should trigger `/gsd-research-phase` before planning to verify upstream APIs and current grammar.

## Phases

**Phase Numbering:**
- Integer phases (0, 1, 2...): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 0: Foundation & Governance** - Repo scaffold, license CI, stack verification spike, doctor stub
- [x] **Phase 1: Parsing Pipeline** - Read `semantic_manifest.json` + `manifest.json`, build ref/source sentinel map
- [x] **Phase 2: Export SQL Generation** - MetricFlow engine integration + sentinel-prefix `ref()` rewriting (HIGH RISK)
- [x] **Phase 3: File Output + CLI** - Atomic byte-stable writer, `generate` / `check` commands, companion YAML
- [x] **Phase 4: Snowflake Adapter** - `sem__*.sql` via `dbt_semantic_view` package (HIGH RISK)
- [x] **Phase 5: Pre-Commit Hook** - `.pre-commit-hooks.yaml` triggering regeneration on YAML changes
- [x] **Phase 6: Databricks Adapter** - `sem__*.sql` via `dbt-databricks` metric view materialisation (dbt-databricks >= 1.12.0, GA)
- [ ] **Phase 7: DuckDB Adapter** - `sem__*.sql` via `duckdb-semantic-views` extension
- [ ] **Phase 8: v1 Release Hardening** - Docs, versioning, publish pipeline, final verification

## Phase Details

### Phase 0: Foundation & Governance
**Goal**: A green repo with verified dependencies, license-clean CI, and enough CLI shape to fail loudly when misconfigured
**Depends on**: Nothing (first phase)
**Requirements**: CLI-03
**Success Criteria** (what must be TRUE):
  1. `uv run pytest` passes on an empty test suite in a freshly cloned repo
  2. CI runs ruff, basedpyright, and pytest (NO license scan — superseded by CONTEXT.md; ELv2 risk is architectural, not a dep-tree problem)
  3. `dbt-s-layer --help` and `dbt-s-layer doctor --help` print usage from a pip-installed build
  4. `dbt-s-layer doctor` detects presence/absence of the `dbtf` binary and reports the installed metricflow version
  5. Pinned versions for `metricflow`, `metricflow-semantics`, and `dbt-semantic-interfaces` are committed after the stack verification spike confirms Pydantic v1/v2 status (as of metricflow 0.213.0 the latter two are bundled in the metricflow wheel, so one `metricflow==` pin covers all three)
**Plans**: 4 plans
  - [x] 00-01-PLAN.md — Package scaffold: pyproject.toml (uv_build, dev deps, scripts entrypoint), src/dbt_s_layer/ with py.typed, tests/ smoke, LICENSE, README (Wave 1)
  - [x] 00-02-PLAN.md — CLI + doctor command: Typer app, _doctor.py (shutil.which + importlib.metadata), CliRunner tests [CLI-03] (Wave 2)
  - [x] 00-03-PLAN.md — Stack verification spike + metricflow family pinning in pyproject.toml, [tool.uv] override-dependencies (Wave 2)
  - [x] 00-04-PLAN.md — .pre-commit-config.yaml + 4 GitHub Actions workflows (ci/quality/release/weekly) on 3.11/3.12/3.13/3.14 matrix (Wave 3)

### Phase 1: Parsing Pipeline
**Goal**: A Python pipeline that loads `semantic_manifest.json` + `manifest.json` from any dbt project and produces typed internal models plus a fully-resolved ref/source sentinel map
**Depends on**: Phase 0
**Requirements**: PARSE-01, PARSE-02, PARSE-03, CLI-05
**Success Criteria** (what must be TRUE):
  1. Given a legacy-spec fixture project (parsed by dbt Core; Fusion 2.x ignores the legacy spec), the pipeline produces an internal `SemanticProject` object with all semantic models, metrics, and saved queries loaded
  2. Given a new-spec fixture project parsed by Fusion or by dbt Core 1.12+, the same pipeline produces the equivalent internal object with no code path differences
  3. Every `semantic_model.node_relation.relation_name` is mapped to a `ref()` / `source()` sentinel via `manifest.json` `depends_on.nodes` lookup
  4. Running any CLI command with a stale or missing `semantic_manifest.json` exits with a clear error telling the user to run `dbt parse` / `dbtf parse`
**Plans**: 4 plans
  - [x] 01-01-PLAN.md — Fixture corpus: real artifacts from Fusion 2.0.8 (new spec) and dbt Core 1.12.6 (new + legacy spec), regen script, corpus guard tests (Wave 1)
  - [x] 01-02-PLAN.md — Artifact readers + `SemanticProject` loader via metricflow's dbt manifest parser [PARSE-01, PARSE-02] (Wave 2)
  - [x] 01-03-PLAN.md — Ref map: semantic model -> dbt node, verbatim `ref()` expression, relation parts [PARSE-03] (Wave 3)
  - [x] 01-04-PLAN.md — Missing/stale artifact errors, dropped-semantic-model warning, `doctor --project-dir` [CLI-05, CLI-03] (Wave 3)

### Phase 2: Export SQL Generation
**Goal**: Given a parsed `SemanticProject`, emit MetricFlow-rendered SQL for every `saved_query` export, with all physical table references rewritten to dbt `{{ ref() }}` / `{{ source() }}` / versioned / cross-project Jinja calls
**Depends on**: Phase 1
**Requirements**: EXPRT-01, EXPRT-02, EXPRT-03, EXPRT-04, EXPRT-05
**Success Criteria** (what must be TRUE):
  1. For each `export` in a `saved_query`, the pipeline produces a rendered SQL string containing the expected aggregations and group-bys
  2. All four sentinel forms (`__REF__`, `__SOURCE__`, `__XREF__`, `__REFV__`) round-trip correctly from sentinel-prefixed SQL to final Jinja calls across a hostile test fixture (3-part Snowflake identifiers, versioned refs, cross-project refs). `__SOURCE__` is reserved only: dbt does not let a semantic model read a source (02-RESEARCH.md)
  3. Pre-flight aborts with a clear error if a user-defined dbt model or source name collides with any reserved sentinel prefix
  4. Post-flight refuses to emit any generated file containing an unresolved sentinel token
  5. The generated `{{ config(materialized=...) }}` block reflects each export's YAML materialisation strategy (`table`, `view`)
**Plans**: 3 plans
- [x] 02-01-PLAN.md — Export fixtures from dbt Core and Fusion (package, versioned models, time spine)
- [x] 02-02-PLAN.md — Sentinel table, pre-flight, ref rewrite, post-flight, Jinja escaping
- [x] 02-03-PLAN.md — Export renderer: dialects, one engine per saved query, config block
**Research flag**: HIGH RISK — `/gsd-research-phase` required before planning. Must verify `MetricFlowEngine.explain()`, `SqlTable.from_string`, and `visit_table_node` API shapes against the pinned metricflow versions.

### Phase 3: File Output + CLI
**Goal**: A deterministic, atomic file writer and a production-grade CLI that can generate, check for drift, and accept all project-configuration flags
**Depends on**: Phase 2
**Requirements**: OUTPT-01, OUTPT-02, OUTPT-03, OUTPT-04, OUTPT-05, CLI-01, CLI-02, CLI-04
**Success Criteria** (what must be TRUE):
  1. `dbt-s-layer generate --project-dir . --output-dir models/semantic/` writes `sem__*.sql`, `exp__*.sql`, and `_semantic_views.yml` to the configured directory (`sem__*.sql` arrive with Phase 4; the writer already owns the prefix)
  2. Running `generate` twice on identical inputs produces byte-identical outputs (sorted keys, stable ordering, no timestamps)
  3. A partial/interrupted run never leaves the output directory in an inconsistent state (writes staged to a temp dir and `os.replace`d atomically)
  4. Every generated file begins with an auto-generated header comment identifying the source semantic model or saved query
  5. `dbt-s-layer check` exits non-zero when committed generated files differ from a fresh re-generation, suitable for CI gating
  6. `--project-dir`, `--output-dir`, `--target`, and `--adapter` flags are all honoured with sensible defaults
**Plans**: 3 plans
- [x] 03-01-PLAN.md — Render, apply and diff generated files
- [x] 03-02-PLAN.md — `generate` / `check`, `--target` / `--adapter`, clashes with user models
- [x] 03-03-PLAN.md — `doctor --output-dir`, README, end-to-end check

### Phase 4: Snowflake Adapter
**Goal**: Every `semantic_model` in the user's YAML produces a `sem__*.sql` model that materialises as a Snowflake `SEMANTIC VIEW` via the `dbt_semantic_view` package, with correct DAG edges to upstream tables
**Depends on**: Phase 3
**Requirements**: SEMVW-01, SEMVW-02, SEMVW-05
**Success Criteria** (what must be TRUE):
  1. For each `semantic_model`, a `sem__<name>.sql` file is generated that invokes the `dbt_semantic_view` package materialisation
  2. Entities, dimensions, and measures in the generated config match the semantic YAML definitions
  3. Every upstream table reference in a generated semantic view model is expressed as `{{ ref(...) }}` or `{{ source(...) }}` so `dbt ls` and lineage tools see the correct DAG edges
  4. `dbt compile --target snowflake` succeeds on a fixture project whose generated `sem__*.sql` files are the only semantic models
**Plans**: 4 plans
- [x] 04-01-PLAN.md — Adapter-neutral semantic view IR and builder (join closure, element mapping, skips)
- [x] 04-02-PLAN.md — Snowflake renderer, name shadowing check, snapshots
- [x] 04-03-PLAN.md — `sem__` files in `generate`/`check`, clashes, warnings, package check, `doctor`
- [x] 04-04-PLAN.md — Snowflake fixtures with `dbt_semantic_view`, compile test on both engines, README
**Research**: done (04-RESEARCH.md). The package takes the raw `CREATE SEMANTIC VIEW` clause list, not a config schema, so criterion 2's "generated config" is the DDL body.

### Phase 5: Pre-Commit Hook
**Goal**: Users can install one pre-commit entry that keeps their committed generated files perfectly in sync with edits to semantic YAML
**Depends on**: Phase 3 (needs `generate`; independent of Phase 4/6/7 adapter completeness)
**Requirements**: HOOK-01, HOOK-02
**Success Criteria** (what must be TRUE):
  1. A project that adds the `dbt-s-layer` hook to `.pre-commit-config.yaml` automatically regenerates files when any YAML containing `semantic_models:`, `metrics:`, or `saved_queries:` keys is modified
  2. The hook completes within 5 seconds with dbt Fusion on a project with up to 100 semantic models and 50 exports; with dbt Core the hook is bounded by Core's own `dbt parse` time (documented in README)
  3. The hook is deterministic: running it twice in a row makes no further changes (exits clean)
**Plans**: 3 plans
- [x] 05-01-PLAN.md — Export rendering speed: one engine set-up, fresh planner per saved query, process pool
- [x] 05-02-PLAN.md — `dbt-s-layer pre-commit`: relevant files, project discovery, `dbt parse`, generate, exit codes
- [x] 05-03-PLAN.md — `.pre-commit-hooks.yaml`, scale test, README, end to end with pre-commit and prek
**Research**: done (05-RESEARCH.md). The hook parses (Fusion or dbt Core) and then generates. Criterion 2 holds with Fusion (4.0 to 4.3 s); dbt Core's own parse takes 5.4 s or more on the 100/50 project, so with Core the hook takes 10 to 12 s.

### Phase 6: Databricks Adapter
**Goal**: Every `semantic_model` produces a `sem__*.sql` model that materialises as a Databricks metric view via the `dbt-databricks` package (`dbt-databricks >= 1.12.0` (GA), databricks/dbt-databricks#1285), with correct DAG edges
**Depends on**: Phase 4 (shares the adapter dispatch machinery)
**Requirements**: SEMVW-03
**Success Criteria** (what must be TRUE):
  1. For each `semantic_model`, a `sem__<name>.sql` file is generated that invokes the `dbt-databricks` metric view materialisation
  2. Entities, dimensions, and measures in the generated config match the semantic YAML definitions
  3. Every upstream table reference is expressed as `{{ ref(...) }}` or `{{ source(...) }}` so DAG edges are correct
  4. `dbt run --target databricks` on a fixture project materialises every `sem__*.sql` as a Databricks metric view
**Plans**: 1 plan
- [x] 06-01-PLAN.md — IR, renderer, dispatch and doctor, fixtures and compile test, README
**Research flag**: MEDIUM — metric views shipped GA in dbt-databricks 1.12.0 (latest 1.12.7); verify the metric view config schema before planning.

### Phase 7: DuckDB Adapter
**Goal**: DuckDB users (local dev, test) can materialise semantic models via the `duckdb-semantic-views` extension, using the same adapter-dispatch pattern as Snowflake
**Depends on**: Phase 4 (shares adapter dispatch)
**Requirements**: SEMVW-04
**Success Criteria** (what must be TRUE):
  1. For each `semantic_model`, a `sem__<name>.sql` file is generated that invokes the `duckdb-semantic-views` extension materialisation
  2. `dbt run --target duckdb` on a fixture project materialises every `sem__*.sql` as a queryable DuckDB semantic view
  3. The same semantic YAML produces working output on both Snowflake and DuckDB with only a `--target` flag change
**Plans**: TBD

### Phase 8: v1 Release Hardening
**Goal**: dbt-s-layer is publishable to PyPI with documentation, versioning, and CI sufficient for real-world adoption
**Depends on**: Phase 5, Phase 6, Phase 7
**Requirements**: (none new — hardens prior work)
**Success Criteria** (what must be TRUE):
  1. `pip install dbt-s-layer` from a dry-run TestPyPI build succeeds on Python 3.11 / 3.12 / 3.13
  2. README walks a user through install → `dbt parse` → `dbt-s-layer generate` → `dbt run` end-to-end on a sample project
  3. CI runs the full integration matrix (Snowflake fixture, DuckDB fixture, Databricks stub path) green on every PR
  4. SemVer 1.0.0 tag is cut with a CHANGELOG entry and release notes
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0. Foundation & Governance | 4/4 | Complete | 2026-10-19 |
| 1. Parsing Pipeline | 4/4 | Complete | 2026-10-19 |
| 2. Export SQL Generation | 3/3 | Complete | 2026-10-19 |
| 3. File Output + CLI | 3/3 | Complete | 2026-10-19 |
| 4. Snowflake Adapter | 4/4 | Complete | 2026-10-19 |
| 5. Pre-Commit Hook | 3/3 | Complete | 2026-10-19 |
| 6. Databricks Adapter | 1/1 | Complete | 2026-10-20 |
| 7. DuckDB Custom Materialisation | 0/TBD | Not started | - |
| 8. v1 Release Hardening | 0/TBD | Not started | - |

---
*Roadmap created: 2026-04-18*
*Granularity: standard (9 phases)*
*Coverage: 25/25 v1 requirements mapped*
