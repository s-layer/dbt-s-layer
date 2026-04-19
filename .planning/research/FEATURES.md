# Feature Landscape

**Domain:** Python tooling for the open dbt Semantic Layer — code-generating warehouse-native semantic objects (Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB semantic views) and pre-aggregated exports for dbt Core / dbt Fusion.
**Researched:** 2026-04-18
**Researcher confidence:** MEDIUM overall — strong grounding in the PROJECT.md + codegen design notes; Context7/WebSearch were unavailable in this session, so competitive-landscape items (dbt Cloud exports, dbt-metricflow CLI, Snowflake-Labs `dbt_semantic_view`, Databricks metric views) are drawn from training data. Flagged per-item below.

---

## Landscape Snapshot (what users do today without this tool)

Understanding table-stakes vs differentiators requires pinning down what the replacement target (dbt Cloud) actually does, and what "manual" looks like on each warehouse. Confidence per claim is marked inline.

### dbt Cloud exports (primary replacement target)

**Confidence: MEDIUM** (training data; not re-verified this session)

- `saved_query` with an `exports:` block is parsed and, on dbt Cloud jobs, produces one **materialised relation** per export — table, view, or cache — in the warehouse.
- The materialisation runs on MetricFlow-compiled SQL. Users never see the SQL, and the relation is not a visible node in `dbt ls` output unless Cloud surfaces it.
- Schema/database routing follows the export config; materialisation strategy (`table` / `view` / `cache`) is per-export.
- Refresh is tied to the dbt Cloud job schedule. No bring-your-own-orchestrator story for Core users.
- **Gap for Core users:** There is no `dbt run` path that materialises an export from a `saved_query`. MetricFlow can *compile* query SQL in Core, but nothing writes it into the DAG as a model.

### dbt-metricflow CLI

**Confidence: MEDIUM** (training data)

- `dbt sl query --metrics … --group-by …` returns query results (client-side), `dbt sl list metrics`, `dbt sl validate-configs`, etc.
- Compiles and *executes* queries against the warehouse; does not write models or materialise results.
- No codegen. No pre-aggregation. No warehouse-native semantic object emission.
- Assumes legacy spec on Core; new-spec (dbt 2.x / Core 1.12+) semantic YAML requires dbt Fusion to parse in the non-Cloud case.

### Snowflake `SEMANTIC VIEW` — manual workflow today

**Confidence: MEDIUM** (training data; the Snowflake-Labs `dbt_semantic_view` package is known to exist but I have not re-verified the current feature set)

- Hand-write `CREATE SEMANTIC VIEW` DDL, or author a model using the Snowflake-Labs `dbt_semantic_view` dbt package's custom materialisation.
- Either way, the **source of truth becomes Snowflake-flavoured YAML or raw DDL**, not dbt semantic YAML. Users maintaining a semantic layer for Cloud/Fusion plus Snowflake-native semantic views duplicate definitions.
- Entities, dimensions, measures, relationships must be re-expressed in Snowflake's syntax (close to dbt's model but not identical).

### Databricks metric views — manual workflow today

**Confidence: LOW** (training data; Databricks metric views may be preview/GA-varying; upstream dbt package is "in progress" per PROJECT.md)

- Hand-authored Unity Catalog metric view definitions, or wait for an upstream dbt package.
- No first-class dbt integration today per PROJECT.md; dbt-s-layer's v1 plan is a stub + warning.

### DuckDB semantic views

**Confidence: LOW** — DuckDB has no built-in semantic view primitive; PROJECT.md describes a project-internal custom materialisation cloning Snowflake's semantics.

### Other tools in adjacent territory

**Confidence: LOW** (could not web-search this session)

- **`dbt-loom`** — only known real-world user of dbt's plugin API; injects synthetic nodes. *Adjacent, not overlapping.* Reference for the "why codegen" decision.
- **Cube / Semantic layer startups (AtScale, Looker LookML, etc.)** — Standalone semantic layers; do not target "read dbt YAML, emit dbt models."
- **`dbt-semantic-interfaces` / MetricFlow** — libraries this project *consumes*, not competes with.
- **No known OSS tool code-generates dbt models from `saved_query` exports for Core.** If one exists it post-dates this researcher's training data — flag as validation item before launch.

---

## Table Stakes

Features users expect from a "dbt-s-layer" package. Missing any of these = the product doesn't solve the problem.

| # | Feature | Why Expected | Complexity | Notes |
|---|---------|--------------|------------|-------|
| 1 | **Parse dbt semantic manifest (new + legacy spec)** | Without this nothing else works. Must handle both dbt 1.x and 2.x / Core 1.12+ YAML. | High | Per PROJECT.md: Fusion binary `dbtf parse` for general case; pure-Python `dir_to_model` fast-path for legacy-spec projects. |
| 2 | **Generate `sem__<name>.sql` semantic view models per `semantic_model`** | Core value prop #1 (warehouse-native semantics). | High | Adapter-dispatched DDL; delegates to `dbt_semantic_view` (Snowflake), stub (Databricks), custom mat (DuckDB). |
| 3 | **Generate `exp__<saved_query>__<export>.sql` export models** | Core value prop #2 (exports for Core). Replaces dbt Cloud exports. | High | Uses `MetricFlowEngine.explain()` for SQL rendering. |
| 4 | **Rewrite physical table refs to `{{ ref() }}` / `{{ source() }}` in generated export SQL** | Without this, generated models have no DAG edges. Silent incorrectness is worse than failure. | High | Sentinel-prefix approach per PROJECT.md. |
| 5 | **Materialisation strategy honoured per export config** | `table` / `view` / `incremental` from YAML must survive codegen. | Medium | Emit `config(materialized='…')` in generated file header. |
| 6 | **Generate companion `_semantic_views.yml` schema file** | dbt parse requires columns / descriptions to be discoverable; users expect docs continuity. | Medium | Source descriptions from semantic YAML. |
| 7 | **CLI entrypoint `dbt-s-layer generate --project-dir . --output-dir models/semantic/`** | Table stakes for any Python codegen tool. | Low | |
| 8 | **Pre-commit hook** | Without this, users hand-run the CLI and generated files drift out of sync silently. This is the seam where "just use dbt Cloud" bites hardest. | Medium | Detect YAML changes that include `semantic_models:` / `metrics:` / `saved_queries:` blocks. |
| 9 | **Generated files are idempotent & diff-clean** | Regenerating must produce byte-identical output given identical input. Non-deterministic output breaks pre-commit, PR review, CI. | Medium | Stable key ordering, trailing newline policy, no timestamps in output. |
| 10 | **Works on dbt Core ≥ 1.7 AND dbt Fusion** | Scoped compatibility promise in PROJECT.md. Fusion users have *nowhere else* to go for this. | Medium | Codegen is engine-agnostic; validated at test time against both. |
| 11 | **Clear failure modes when Fusion binary missing** | Fusion binary is required for new-spec projects. Opaque errors here will generate issues on day one. | Low | Detect & emit an actionable error: "install dbt Fusion (https://…) or downgrade your semantic YAML to legacy spec." |
| 12 | **Snowflake: full support out of the box** | Snowflake is the primary v1 target per PROJECT.md. | High | Delegates DDL emission to `dbt_semantic_view` (Snowflake-Labs). |
| 13 | **Databricks: graceful stub with warning** | Upstream package not ready. Silent no-op or a crash are both worse than a clear "not yet supported, stubbed." | Low | Emit a placeholder model + warning with tracking issue link. |
| 14 | **DuckDB: custom materialisation works for local dev / tests** | DuckDB is the de-facto dbt Core dev warehouse. If `dbt build` breaks on DuckDB, adoption stalls. | High | Project-internal custom materialisation cloning Snowflake shape. |
| 15 | **Apache-2.0 / MIT license; no ELv2 contamination** | Users will check. Subprocess boundary with Fusion binary matters legally; the package itself must not import ELv2 code. | Low | Document. Enforce via lint/CI that no ELv2 crate is a Python dependency. |

### Table-stakes dependency graph

```
(1) Parse manifest
     ├─> (2) Generate sem__*.sql ──> (12)(13)(14) per-adapter materialisation
     └─> (3) Generate exp__*.sql
              └─> (4) ref() rewriting (blocks (3) being useful)
                    └─> (5) Materialisation strategy in header
(2)(3)(6) ──> (9) deterministic output ──> (8) pre-commit hook
(7) CLI entry is prerequisite for (8) and for users in general
(10)(11)(15) are cross-cutting constraints on the whole pipeline
```

Feature 4 (ref() rewriting) is the single highest-risk table-stake item — if it's unreliable, every export model has broken DAG edges.

---

## Differentiators

Features that set dbt-s-layer apart from dbt Cloud exports and from hand-rolled solutions. Not required for MVP, but shift the value proposition from "Cloud replacement" to "Core-native advantage."

| # | Feature | Value Proposition | Complexity | Notes |
|---|---------|-------------------|------------|-------|
| D1 | **Generated files are first-class DAG nodes** (visible to `dbt ls`, selection syntax, lineage tools, exposures) | dbt Cloud exports are opaque jobs; here, an export is a normal model you can `dbt build --select +exp__revenue_by_region`. This is a genuine DX improvement over Cloud. | Free (falls out of codegen approach) | Market this explicitly in README. |
| D2 | **Git-transparent generated SQL** | Users can read, diff, review, and cherry-pick generated SQL in PRs. Cloud hides SQL; here it's in the repo. Also a debugging accelerant. | Free | |
| D3 | **Warehouse-native semantic objects (not just exports)** | dbt Cloud does not emit Snowflake `SEMANTIC VIEW` / Databricks metric views from dbt YAML. This tool does. That's a capability Cloud *lacks*. | High (already in MVP) | Arguably the single strongest reason to pick this tool over Cloud. |
| D4 | **Multi-dialect from one semantic YAML** | Maintain one set of dbt semantic models; get Snowflake, Databricks, and DuckDB artifacts. Fits dev-on-DuckDB / prod-on-Snowflake pattern. | Medium | Already in architecture. |
| D5 | **Pluggable adapter dispatch for future warehouses** | BigQuery, Redshift, Postgres, etc. can add materialisations later without forking. | Medium | Keep DDL emission behind a `SemanticAdapter` interface from day one even if only Snowflake is live. |
| D6 | **Incremental export materialisation** | MetricFlow's compiled SQL is full-refresh. An incremental predicate layer (partition/date filter injection on top of the compiled SQL) is not available in dbt Cloud today (confidence LOW on current Cloud behaviour — verify before marketing). | High | Out of scope for v1 per PROJECT.md; obvious v2 differentiator. |
| D7 | **Contracts + column docs auto-generated from semantic types** | Dimensions and measures have types in semantic YAML. Generating dbt contracts + column `description:` automatically is high-leverage docs work users won't do by hand. | Medium | PROJECT.md marks this as a v2 iteration. |
| D8 | **Selective regeneration / diff-based codegen** | On large projects, regenerating every file on every YAML touch makes pre-commit slow. Emitting only changed files scales. | Medium | PROJECT.md flags as open question; v1 can be full regen, v2 diff. |
| D9 | **`dbt-s-layer check` command for CI** | Validate that generated files match what YAML *would* generate. Catches hand-edited generated files, stale regenerations, merge conflicts. | Low | High value for pre-commit / CI enforcement. |
| D10 | **Rich docstring/description propagation** | Pull descriptions from semantic YAML into generated SQL comments and into `_semantic_views.yml`, so `dbt docs` shows human descriptions without redefining them. | Low | Cheap, visible polish. |
| D11 | **Fusion-native first** | Because codegen works equally on Core and Fusion, this tool has first-class Fusion support. Many competing tools are Core-only. | Free (inherent to approach) | Emphasise in positioning. |
| D12 | **No runtime state / no plugin API coupling** | Zero reliance on `dbt.plugins.get_nodes()` — stable across dbt minor releases, no breakage risk. | Free | |
| D13 | **Typed Python API (not just CLI)** | Allow programmatic invocation — e.g., from custom orchestrators, from CI scripts, from IDE integrations. | Low | Cheap if CLI is a thin wrapper over a library. |

---

## Anti-Features

Things to deliberately NOT build. Each one is a siren-call feature that would either bloat scope, duplicate existing tooling, or re-create dbt Cloud.

| # | Anti-Feature | Why Avoid | What to Do Instead |
|---|--------------|-----------|--------------------|
| A1 | **Run the generator inside a dbt macro / custom materialisation at parse-time** | dbt macros have no filesystem write access; would require unsupported tricks. Also re-creates the exact complexity the codegen approach sidesteps. | Standalone Python CLI triggered by pre-commit or manually. |
| A2 | **Use `dbt.plugins.get_nodes()` to inject synthetic nodes** | Undocumented API, no Fusion equivalent, only `dbt-loom` uses it. Locks the tool to Core. | Codegen to disk (committed files). |
| A3 | **Re-implement MetricFlow's SQL compiler** | Years of work already done upstream. Every divergence is a correctness bug. | Call `MetricFlowEngine.explain()` as a black box. |
| A4 | **Query execution / "like `dbt sl query`"** | That's what `dbt-metricflow` does. Duplicating it splits the ecosystem. | Out of scope. Compose with `dbt-metricflow` if needed. |
| A5 | **A custom semantic YAML dialect / config file (`s-layer.yml`-style superset)** | Forks the semantic YAML format users maintain for Cloud/Fusion. Maintenance burden forever. **Note:** the repo has a todo "Define s-layer.yml scope and format" — if this is about per-project tool config (output dir, adapter overrides), it's fine; if it's an alternative authoring format, it's an anti-feature. | Read standard dbt semantic YAML as the *only* source of truth. Any package config lives in `dbt_project.yml` under `vars:` or in a minimal tool-config file strictly for codegen knobs. |
| A6 | **YAML translation tool (dbt ↔ Snowflake/Databricks/DuckDB semantic YAML)** | Already explicitly out of scope in PROJECT.md. Big, distinct tool with its own value case — shouldn't bundle. | Future milestone / separate package. |
| A7 | **dbt Cloud integration / publishing to Cloud** | Cloud already has exports natively; integrating creates confusion and maintenance. | Explicit non-target. |
| A8 | **GUI / web UI for authoring semantic YAML** | Huge scope creep, abundant alternatives (Cloud UI, editor plugins). | Users author YAML in their editor. |
| A9 | **"Smart" regeneration that edits hand-written generated files** | Guaranteed merge hell. Generated files must be fully authoritative (clobbered on regen). | Pre-commit regenerates; hand-edits are destroyed by design. Document in README. |
| A10 | **Supporting dbt Core < 1.7** | Semantic models are not GA below 1.7 per PROJECT.md. Support scope creep for a shrinking audience. | Document minimum version; clear error if below. |
| A11 | **Embedding ELv2-licensed code (Fusion crates) in the package** | License contamination for downstream commercial users. | Subprocess boundary only. Apache-2.0 / MIT package itself. |
| A12 | **Generating docs sites or BI config (Looker views, Tableau datasources, etc.)** | Adjacent and tempting, but a separate tool each. Distracts from the core "codegen for dbt" mission. | Emit enough metadata (descriptions, types) in `_semantic_views.yml` that downstream tools can consume; don't write those tools here. |
| A13 | **Supporting warehouses without a path to a semantic object** | Generating a plain SQL view and calling it a "semantic view" misleads users; they think they have Snowflake-grade semantics when they don't. | Only ship an adapter when either (a) warehouse has a native semantic object, or (b) an intentional "flat SQL fallback" mode is explicitly documented with clear caveats. |
| A14 | **Caching / memoisation of generated output between runs** | Generator is cheap; caching adds complexity and subtle correctness bugs (stale manifest, changed adapter config). | Always regenerate from scratch inside the CLI; rely on pre-commit's own staging to avoid redundant runs. |

---

## Feature Dependencies

```
[Manifest parser (TS1)]
   └─> [sem__*.sql generator (TS2)]
          ├─> [Snowflake adapter (TS12)]
          ├─> [Databricks stub (TS13)]
          └─> [DuckDB custom mat (TS14)]
   └─> [exp__*.sql generator (TS3)]
          └─> [ref()/source() rewriter (TS4)]        ← highest-risk item
                └─> [Materialisation strategy in header (TS5)]
   └─> [_semantic_views.yml emitter (TS6)]

[CLI entry (TS7)] ─> [Pre-commit hook (TS8)]
                   └─> [Determinism (TS9)]           ← pre-commit demands this

[D9 check command] depends on TS1..TS9 (must re-run generation to diff)
[D7 contracts/docs] depends on TS1 + TS6
[D6 incremental exports] depends on TS3 + TS5
[D8 diff-based codegen] depends on TS9 (need stable output to diff)

[TS10 Core ≥1.7 + Fusion compat], [TS11 clear failures], [TS15 licensing] are cross-cutting.
```

Critical path for MVP: `TS1 -> TS2 (Snowflake branch) -> TS3 -> TS4 -> TS5 -> TS6 -> TS9 -> TS7 -> TS8`. Everything else is schedule float.

---

## MVP Recommendation

**Ship-to-validate MVP — minimum set that a dbt Core user on Snowflake can adopt to replace Cloud exports and gain Snowflake semantic views.**

### Must ship (in order)

1. **TS1** Parse dbt semantic manifest via Fusion binary (`dbtf parse`), with the legacy-spec Python fast-path.
2. **TS2 + TS12** Generate `sem__*.sql` for Snowflake using `dbt_semantic_view` package.
3. **TS3 + TS4** Generate `exp__*.sql` using `MetricFlowEngine.explain()` with sentinel-prefix `ref()`/`source()` rewriting.
4. **TS5** Materialisation strategy honoured.
5. **TS6** `_semantic_views.yml` schema file emission.
6. **TS9** Deterministic output (byte-stable regeneration).
7. **TS7** CLI.
8. **TS8** Pre-commit hook.
9. **TS11** Clear error on missing Fusion binary.
10. **TS14** DuckDB custom materialisation (required for local dev / test story; if deferred, document it explicitly as "Snowflake-only in v1 — DuckDB dev deferred to v1.1").
11. **TS13** Databricks stub + warning.
12. **TS10, TS15** Compat + licensing are validated via CI, not separate features.

### First differentiator to ship alongside MVP

- **D9 (`dbt-s-layer check`)** — cheap, huge value for CI and pre-commit enforcement. Recommend including in v1 rather than v2.
- **D1 + D2 (first-class DAG, git-transparent SQL)** — these *fall out of* the codegen approach. Zero extra work. **Market them.**
- **D11 (Fusion-native first)** — same: free, market it.

### Defer to v2

- D6 Incremental exports (real design work; PROJECT.md out-of-scope for v1)
- D7 Contracts + column docs from semantic types
- D8 Diff-based codegen
- D13 Typed Python API (can emerge after CLI stabilises)

### Defer indefinitely / revisit

- D5 Multi-warehouse adapter expansion — pull demand; not push
- D12 is already implicit

---

## Positioning Summary (for README / launch copy)

- **vs dbt Cloud exports:** Same feature, no subscription, plus your exports are first-class dbt models with full DAG lineage.
- **vs `dbt-metricflow` CLI:** Complementary. That tool *queries*; this tool *materialises*.
- **vs Snowflake-Labs `dbt_semantic_view`:** Complementary. That package materialises; this tool generates the model from your existing dbt semantic YAML so you don't write Snowflake-native YAML by hand.
- **vs writing it yourself:** You keep one source of truth (dbt semantic YAML), get multi-dialect output, and pre-commit keeps everything in sync.

---

## Open Validation Items Before Launch

The following claims depend on training data and should be re-verified (ideally via Context7 + current docs) before going into public-facing README/marketing:

1. **Exact behaviour of dbt Cloud `exports:`** — materialisation types supported, cache semantics, node visibility. (MEDIUM confidence)
2. **`dbt-metricflow` CLI current capabilities** — confirm it still does not codegen or materialise. (MEDIUM confidence)
3. **Snowflake-Labs `dbt_semantic_view` current state** — materialisation name, input YAML shape expected, supported warehouse version range. (MEDIUM confidence)
4. **Databricks metric view availability** — GA status, whether an upstream dbt package has landed since PROJECT.md was written. (LOW confidence)
5. **Any new OSS tool in this niche since late 2025** — search "dbt core semantic layer exports codegen" on GitHub + HN. (LOW confidence — could not verify this session)
6. **D6 incremental exports in dbt Cloud** — verify Cloud has not added this before claiming it as a differentiator. (LOW confidence)

---

## Sources

- `/Users/paul/Documents/Dev/Personal/dbt-s-layer/.planning/PROJECT.md` — authoritative for scope, decisions, and out-of-scope list (HIGH confidence).
- `/Users/paul/Documents/Dev/Personal/dbt-s-layer/_notes/dbt-semantic-views-codegen.md` — authoritative for generator design, DAG shape, and integration rationale (HIGH confidence).
- Training data for dbt Cloud, dbt-metricflow, MetricFlow, Snowflake `SEMANTIC VIEW`, Databricks metric views, `dbt-loom`, `dbt-semantic-interfaces` (MEDIUM-LOW confidence; flagged per item).
- Context7 and web search were **not available** in this session — the validation items list above must be worked through before launch.
