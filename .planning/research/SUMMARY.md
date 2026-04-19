# Project Research Summary

**Project:** dbt-s-layer
**Domain:** Python codegen tooling for the open dbt Semantic Layer (Core + Fusion) — emits warehouse-native semantic objects (Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB) and pre-aggregated export models
**Researched:** 2026-04-19
**Confidence:** MEDIUM — HIGH on shape/approach, MEDIUM/LOW on specific upstream versions & third-party package internals (external verification tools were unavailable this research pass)

## Executive Summary

dbt-s-layer is a Python **codegen tool** that sits beside `dbt-core` / `dbt Fusion` and reads a user's existing dbt semantic YAML (via the Fusion binary's `dbtf parse` output, with an optional pure-Python legacy-spec fast-path) to emit two kinds of files: (a) `sem__*.sql` warehouse-native semantic view models dispatched per adapter (Snowflake via Snowflake-Labs `dbt_semantic_view`, Databricks as a stub + warning, DuckDB via a project-internal custom materialisation), and (b) `exp__*.sql` export models whose SQL is rendered by `MetricFlowEngine.explain()` and then post-processed to replace physical table references with `{{ ref() }}` / `{{ source() }}` Jinja calls. The output is committed to the user's repo and driven by a pre-commit hook — zero runtime coupling, zero `dbt.plugins` API dependency, full DAG transparency.

The recommended stack is the 2026 Python default — `uv` + `hatchling` + `pyproject.toml`, `typer` for CLI, Pydantic v2 for our own data models (with a `pydantic.v1` compat shim *only* if metricflow is still on v1 — **to be verified in phase 0**), `ruamel.yaml` for comment-preserving YAML output, Jinja2 for templates, `syrupy` for snapshot-testing generated SQL/YAML, and `ruff` + `mypy` + `pre-commit` for DX. The three metricflow-family packages (`metricflow`, `metricflow-semantics`, `dbt-semantic-interfaces`) must be pinned as a tested matrix. Fusion is a **subprocess-only** dependency — never linked, never vendored — both to avoid ELv2 license contamination and because no Python bindings exist.

The top risks are (1) the sentinel-prefix rewrite trick depending on two semi-public MetricFlow internals; (2) `semantic_manifest.json` schema drift between Fusion's Rust serialiser and metricflow's Pydantic deserialiser; (3) warehouse-native semantic-object maturity (Snowflake `SEMANTIC VIEW` and Databricks metric views are 2025–2026 features still stabilising); and (4) pre-commit UX — any hook that is slow, non-deterministic, or requires a warehouse connection will be disabled by users. All have specific, testable mitigations.

## Key Findings

### Recommended Stack

- `uv` (≥0.5) + `hatchling` (≥1.25) + `pyproject.toml` — build & dependency management
- `typer` (~0.15) + `rich` — CLI for `dbt-s-layer generate` / `check` / `doctor`
- `pydantic` v2 (≥2.7) — our own config and data types; `pydantic.v1` shim only if metricflow still pins v1 (must verify in Phase 0)
- `metricflow` + `metricflow-semantics` + `dbt-semantic-interfaces` — pin all three as a tested matrix
- `dbt Fusion` binary (`dbtf`) — runtime subprocess dependency; users install themselves; detect via `shutil.which`
- `jinja2` (≥3.1) for SQL/YAML templates; `ruamel.yaml` (≥0.18) for comment-preserving YAML
- `syrupy` (≥4) for snapshot tests against golden SQL fixtures
- `ruff` (≥0.8) + `mypy` (≥1.13) + `pre-commit` (≥3.8)

See STACK.md for full details and the verification checklist to run in Phase 0.

### Expected Features

**Must have (table stakes):**
- Parse dbt semantic manifest (new + legacy spec) via Fusion binary with optional legacy-only Python fast-path
- Generate `sem__*.sql` warehouse-native semantic view models per `semantic_model` (adapter-dispatched)
- Generate `exp__*.sql` export models from `saved_query` with MetricFlow-compiled SQL
- Sentinel-prefix rewrite of physical table refs → `{{ ref() }}` / `{{ source() }}` / xref / versioned — highest-risk item
- Materialisation strategy honoured (`table`/`view`/`incremental`) from YAML into generated `config()` header
- Companion `_semantic_views.yml` schema file emission
- CLI (`dbt-s-layer generate`) + pre-commit hook (`.pre-commit-hooks.yaml` as public surface)
- Deterministic byte-stable output
- Snowflake full support via `dbt_semantic_view` package delegation
- Databricks graceful stub + warning (no silent fallback views)
- DuckDB custom materialisation (required for local dev / test)
- Clear failure modes on missing Fusion binary; Apache-2.0/MIT licensing (no ELv2 contamination)

**Should have (competitive differentiators):**
- `dbt-s-layer check` (CI mode — fail on drift between YAML and generated files)
- First-class DAG nodes + git-transparent SQL (fall out of codegen approach for free)
- Multi-dialect from one semantic YAML (dev-on-DuckDB / prod-on-Snowflake pattern)
- Typed Python API (library-first; CLI is a thin wrapper)

**Explicit anti-features:**
- No query execution (that's `dbt-metricflow`)
- No custom semantic YAML dialect (would fork users' source of truth)
- No ELv2-licensed code embedding
- No `dbt.plugins.get_nodes()` coupling

See FEATURES.md for full table-stakes / differentiator / anti-feature catalogue and open validation items.

### Architecture Approach

The architecture is a linear Python pipeline with narrow, testable component boundaries:

```
dbt project (semantic YAML)
        │
   dbtf parse  (or dir_to_model fast-path for legacy spec)
        │
   semantic_manifest.json + manifest.json
        │
   RefResolver  →  SemanticModelMutator  (sentinel rewriting)
        │
   ExportSqlBuilder + StubSqlClient  →  MetricFlowEngine.explain()
        │
   RefRewriter  (4 sentinel regexes → Jinja)
        │
   SemanticViewDDLBuilder  (adapter-dispatched: Snowflake / Databricks / DuckDB)
        │
   FileWriter  (staging dir + atomic os.replace)
        │
   models/semantic/  sem__*.sql  exp__*.sql  _semantic_views.yml
```

Key patterns: subprocess-only Fusion boundary (license + dependency shape); narrow MetricFlow public-API surface behind a dedicated `metricflow_adapter.py` module; sentinel-prefix rewrite (2–3 part `SqlTable` constraint, verbatim emission, all four sentinels shipped together); idempotent byte-stable writes via staging-dir + atomic replace; two-tier parser (Fusion default, Python fast-path for legacy).

See ARCHITECTURE.md for full component boundaries, data flow, and anti-patterns.

### Critical Pitfalls

1. **MetricFlow public-API drift** — `MetricFlowEngine.explain()`, `SqlTable.from_string`, `visit_table_node` are semi-public. Mitigate: pin exact versions, isolate in `metricflow_adapter.py`, snapshot-test against golden fixtures, gate upgrades on human-reviewed diffs.
2. **Sentinel rewrite edge cases** — 3-part Snowflake identifiers, future quoting, real user tables named `__REF__`, Databricks 3-level namespace. Mitigate: pre-flight scan for collisions; post-flight scan refusing to write files containing sentinel tokens; hostile fixtures; all four sentinels (ref/source/xref/versioned) shipped together in Phase 1.
3. **Manifest schema drift** — Fusion (Rust) and metricflow (Pydantic) evolve independently. Mitigate: version-matrix CI; committed fixture manifests; `dbt-s-layer doctor` validating supported combinations.
4. **Warehouse-native semantic object immaturity** — Snowflake `SEMANTIC VIEW` and Databricks metric views are 2025–2026 features. Mitigate: per-warehouse capability matrix; feature-detection refusing unsupported DDL; Databricks default = no file + warning.
5. **Pre-commit UX** — slow or non-deterministic hooks get disabled. Mitigate: <5s perf budget; deterministic byte-stable output; staging-dir atomicity; `dbt-s-layer check` for CI.
6. **ELv2 licensing contamination** — no vendored Fusion code, no PyO3. Enforce with license-scan CI.

See PITFALLS.md for 29 total pitfalls (6 critical, 11 moderate, 12 minor) with phase-mapped mitigations.

## Implications for Roadmap

Suggested 9-phase structure:

| Phase | Name | Risk | Research needed? |
|-------|------|------|-----------------|
| 0 | Foundation & Governance | LOW | One-hour stack verification spike |
| 1 | Parsing Pipeline | MEDIUM | Verify `dbtf` CLI flags + stub profiles.yml |
| 2 | Export SQL Generation | **HIGH** | **`/gsd-research-phase` strongly recommended** — MetricFlow internals |
| 3 | File Output + CLI + check mode | LOW | Standard patterns |
| 4 | Snowflake Adapter | **HIGH** | **`/gsd-research-phase` strongly recommended** — Snowflake DDL + `dbt_semantic_view` |
| 5 | Pre-Commit Hook | LOW | Standard pre-commit patterns |
| 6 | Databricks Stub | MEDIUM | Re-check upstream package status |
| 7 | DuckDB Custom Materialisation | MEDIUM | Design spike (no upstream precedent) |
| 8 | v1 Release Hardening | LOW | Process phase |

**Phase ordering rationale:** Phases 0→1→2→3 are strictly sequential (each is a hard dependency of the next). Phase 2 (sentinel rewrite) is the highest-risk unit — placed early so failure is cheap to discover. Phases 4, 6, 7 can run in parallel after Phase 3; Snowflake is first as the primary v1 target.

## Gaps to Address

External verification tools (Context7, WebSearch) were not available this pass. Must resolve before coding:

- **metricflow Pydantic version (v1 or v2)** — Phase 0 spike
- **Current stable versions** of metricflow family — Phase 0 spike
- **Fusion GA status + min version** — Phase 0 spike
- **`dbtf parse` CLI flags + stub `profiles.yml` acceptance** — Phase 1
- **Snowflake `CREATE SEMANTIC VIEW` current grammar** + `dbt_semantic_view` config schema — Phase 4 research
- **Databricks metric view GA status + upstream dbt package timeline** — Phase 6
- **Any competing OSS tools in this niche since late 2025** — Phase 8 pre-launch

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack shape | HIGH | Consensus 2026 tools |
| Stack versions | LOW | PyPI queries unavailable — use verification checklist in STACK.md |
| Features | MEDIUM | Internal scope HIGH; competitive claims need live verification |
| Architecture | MEDIUM-HIGH | Internal surfaces cite source file:line references; external package specifics are LOW |
| Pitfalls | MEDIUM-HIGH | Grounded in design notes' own open-question lists |

---
*Research completed: 2026-04-19*
*Ready for roadmap: yes*
