# Architecture Patterns

**Domain:** Python code generator for dbt semantic layer (parses semantic YAML → emits warehouse-native DDL models + pre-aggregated export models)
**Researched:** 2026-04-18
**Overall confidence:** MEDIUM — internal design notes (`_notes/semantic-manifest-loading.md`, `_notes/dbt-semantic-views-codegen.md`) cite specific MetricFlow file:line references, which I treat as HIGH-confidence for internal integration shapes. External tooling references (Context7, WebSearch, WebFetch) were unavailable during this research pass, so claims about Snowflake / Databricks DDL surface area, Fusion CLI flags, and the `dbt_semantic_view` package are grounded in training data and flagged LOW where not verified in the design notes.

---

## Recommended Architecture

### High-Level Shape

```
                     ┌──────────────────────────┐
                     │   User-authored input    │
                     │  (SoT: dbt semantic YAML)│
                     │  semantic_models:        │
                     │  metrics:                │
                     │  saved_queries:          │
                     │     exports:             │
                     └─────────────┬────────────┘
                                   │
          ┌────────────────────────┴────────────────────────┐
          ▼                                                 ▼
┌───────────────────────┐                       ┌──────────────────────────┐
│   Parse layer         │                       │   Fast-path (legacy only)│
│   dbtf parse (subproc)│                       │   dir_to_model (Python)  │
└────────────┬──────────┘                       └───────────────┬──────────┘
             │                                                  │
             ▼                                                  ▼
       target/semantic_manifest.json  ──►  PydanticSemanticManifest (in-memory)
             │                                                  │
             │  + target/manifest.json (for depends_on.nodes)    │
             ▼                                                  │
┌─────────────────────────────────────────────────────────────────────────┐
│                        CORE GENERATOR (Python)                          │
│  ┌───────────────────┐  ┌────────────────────────┐  ┌──────────────┐    │
│  │ RefResolver       │  │ ExportSqlBuilder       │  │ SemanticView │    │
│  │  (manifest →      │  │  MetricFlowEngine      │  │ DDLBuilder   │    │
│  │   sentinel map)   │  │  + stub SqlClient      │  │  (adapter-   │    │
│  │                   │  │  + explain()           │  │   dispatch)  │    │
│  └─────────┬─────────┘  └────────────┬───────────┘  └──────┬───────┘    │
│            │                         │                     │            │
│            ▼                         ▼                     ▼            │
│       sentinel map           raw SQL (physical)       DDL string        │
│                                      │                     │            │
│                                      ▼                     │            │
│                           Regex rewrite                    │            │
│                           (sentinels → ref/source)         │            │
│                                      │                     │            │
│                                      ▼                     ▼            │
│                           ┌────────────────────────────────────────┐    │
│                           │         FileWriter                     │    │
│                           │   models/semantic/sem__*.sql           │    │
│                           │   models/semantic/exp__*.sql           │    │
│                           │   models/semantic/_semantic_views.yml  │    │
│                           └────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
             │
             ▼
       dbt-core / dbt Fusion (unchanged)
             │
             ▼
       Warehouse: Snowflake / Databricks / DuckDB
```

### Component Boundaries

| Component | Responsibility | Communicates With | Boundary Note |
|-----------|---------------|-------------------|---------------|
| **CLI Entrypoint** (`dbt-s-layer generate`) | Parse args, discover project dir, orchestrate generation, emit exit code | User shell; `ProjectResolver`; `GeneratorPipeline` | Thin — no business logic. Wraps pipeline for pre-commit use. |
| **ProjectResolver** | Locate `dbt_project.yml`, resolve model search paths, determine target adapter dialect from profiles | `dbt_project.yml`, `profiles.yml` | Read-only against user project. Does not invoke dbt. |
| **ManifestLoader** | Decide fast-path vs Fusion-path. Shell out to `dbtf parse` or call `dir_to_model`. Deserialize `semantic_manifest.json` → `PydanticSemanticManifest`. Optionally load `manifest.json` for `depends_on.nodes`. | `dbtf` subprocess; `metricflow_semantic_interfaces`; filesystem | **Hard boundary:** subprocess only — no Rust linkage. No ELv2 code is imported. |
| **RefResolver** | For each `semantic_model` in manifest, determine the dbt `ref()`/`source()` call it represents by consulting `manifest.json`'s `depends_on.nodes`. Build a sentinel-name map. | `manifest.json`; `PydanticSemanticManifest` | Pure function: (manifest, node_map) → sentinel substitutions. |
| **SemanticModelMutator** | Before engine construction, mutate `semantic_model.node_relation.relation_name` and `time_spine.node_relation.relation_name` to sentinel strings (`__REF__.stg_orders`, `__SOURCE__.raw.orders`, etc.). | `PydanticSemanticManifest` (in-memory) | Narrow write surface: only mutates two string fields. Reversible via the sentinel map. |
| **ExportSqlBuilder** | For each `saved_query` with exports, build a `MetricFlowQueryRequest`, call `MetricFlowEngine.explain()`, capture the returned SQL. | `metricflow.engine.metricflow_engine.MetricFlowEngine`; stub `SqlClient` | Depends on a narrow MetricFlow public API: `MetricFlowQueryRequest.create`, `engine.explain()`, `SqlClient` protocol. |
| **StubSqlClient** | Satisfy `MetricFlowEngine`'s required `SqlClient` dependency without connecting to any warehouse. Exposes `sql_engine` (dialect) only; raises on execute. | `MetricFlowEngine` (read only) | Protocol conformance only. Zero I/O. |
| **RefRewriter** | Post-render: regex-substitute sentinel tokens in SQL back to Jinja `{{ ref(...) }}` / `{{ source(...) }}`. | String input/output | Pure text transform. Four regexes (REF, SOURCE, XREF, REFV). |
| **SemanticViewDDLBuilder** | For each `semantic_model`, render adapter-specific DDL (Snowflake SEMANTIC VIEW, Databricks metric view, DuckDB semantic view). | `PydanticSemanticManifest`; adapter dispatch registry | Adapter dispatch via the project target dialect. Implementations are independent. |
| **DDLAdapter** (Snowflake / Databricks / DuckDB) | Render one `semantic_model` to DDL string in the target dialect's grammar. | DDLBuilder registry | Three sibling implementations. Snowflake = primary, Databricks = stub + warning, DuckDB = project-internal custom materialisation. |
| **FileWriter** | Write `sem__*.sql`, `exp__*.sql`, `_semantic_views.yml`. Idempotent writes (same content ⇒ no file mtime change). | Filesystem; `models/semantic/` | No git operations. Writes are the tool's only side effect. |
| **PrecommitHook** | Detect YAML changes, invoke CLI, stage regenerated files. | git; CLI entrypoint | Shell wrapper. Identical logic to manual CLI invocation. |
| **FastPathParser** (optional) | For legacy-spec projects: bypass Fusion, use `parse_directory_of_yaml_files_to_semantic_manifest` directly. | `metricflow_semantic_interfaces.parsing.dir_to_model` | Same return type as ManifestLoader's Fusion path. Interchangeable downstream. |

### Data Flow

**Direction: user YAML → generated model files → (later) dbt DAG.** No runtime coupling; the generator's output is static files.

1. **Input phase** — `dbt_project.yml` + semantic YAML files on disk.
2. **Parse phase** — `dbtf parse` writes `target/semantic_manifest.json` and `target/manifest.json`. ManifestLoader reads both.
3. **Resolution phase** — RefResolver cross-references semantic-model nodes with `manifest.json`'s `depends_on.nodes` to build a `{semantic_model_name → sentinel_relation_name}` map. SemanticModelMutator applies this to the in-memory manifest.
4. **Generation phase (parallel per semantic_model / saved_query):**
   - **Export path:** SemanticManifestLookup → MetricFlowEngine → QueryRequest.create(saved_query_name=…) → explain() → raw SQL with sentinel FROM clauses → RefRewriter → final Jinja SQL → FileWriter.
   - **Semantic view path:** For each `semantic_model`, DDLBuilder dispatches by adapter dialect → renders CREATE SEMANTIC VIEW / CREATE METRIC VIEW / DuckDB equivalent → FileWriter.
5. **Output phase** — `models/semantic/sem__*.sql`, `models/semantic/exp__*.sql`, `models/semantic/_semantic_views.yml` written to disk.
6. **Downstream (out of our control)** — dbt parses generated files normally; `ref()`/`source()` calls produce correct DAG edges; `dbt build` runs the models.

**Information flow is unidirectional.** The generator never reads back generated files. It also never reads back the warehouse. Each run is a full regeneration from YAML → disk.

### External System Integration Surfaces

#### dbt Fusion binary (`dbtf`)

Confidence: **MEDIUM** — design note cites `_fusion/crates/dbt-parser/src/resolve/resolve_saved_queries.rs` suggesting direct source inspection. CLI flag surface not re-verified against current Fusion release.

- **Interface:** Subprocess. We invoke with `dbtf parse --project-dir <path>` and consume artifacts from `<path>/target/`. We do **not** link against any Fusion crates.
- **Inputs it needs:** `dbt_project.yml`, semantic YAML files, reachable `profiles.yml` (even for parse-only — see Open Questions in design note).
- **Outputs we consume:**
  - `target/semantic_manifest.json` — the MetricFlow-shaped semantic manifest.
  - `target/manifest.json` — the standard dbt manifest, for `depends_on.nodes` resolution.
- **Licensing boundary:** Fusion is ELv2. Subprocess invocation does not propagate the license. This is a critical boundary we must not cross.
- **Stability risk:** Fusion's Rust `SemanticManifest` shape and MetricFlow's `PydanticSemanticManifest` shape are derived from the same OSI spec but evolve independently. Treated as compatible today; requires a CI fixture test to detect drift.

#### MetricFlow (Apache-2.0 Python library)

Confidence: **HIGH** — design note cites specific files and line numbers:
- `metricflow/dataset/convert_semantic_model.py:514` — single choke point where `node_relation.relation_name` becomes a `SqlTableNode`.
- `metricflow/sql/render/sql_plan_renderer.py:360` — `visit_table_node` emits verbatim.
- `metricflow_semantics/sql/sql_table.py:30` — `SqlTable.from_string` accepts 2 or 3 parts.
- `metricflow_semantic_interfaces/parsing/dir_to_model.py:55-60` — legacy-only parser.
- `metricflow/engine/metricflow_engine.py` — `MetricFlowEngine.explain()`.

- **Public API we rely on:**
  - `PydanticSemanticManifest.parse_file(path)` (Pydantic v1) or `model_validate_json` (v2).
  - `SemanticManifestLookup(manifest)`.
  - `MetricFlowEngine(semantic_manifest_lookup=lookup, sql_client=stub)`.
  - `MetricFlowQueryRequest.create(saved_query_name=…)`.
  - `engine.explain(request).sql_statement.sql`.
  - `SqlClient` protocol (implemented as a stub exposing `sql_engine` only).
- **Private surfaces we depend on (tracked risks):**
  - `SqlTable.from_string` accepting 2 and 3 dot-separated parts.
  - `visit_table_node` rendering `sql_table.sql` verbatim (no quoting, no dialect coercion).
  - `node_relation.relation_name` being a simple writable string field on `SemanticModel` / `TimeSpine`.

#### Snowflake SEMANTIC VIEW (DDL)

Confidence: **LOW** — not verified this pass. Training-data claim: Snowflake GA'd `CREATE SEMANTIC VIEW` supporting TABLES, RELATIONSHIPS, FACTS, DIMENSIONS, and METRICS clauses in 2025. Exact clause grammar and mapping from dbt semantic-model fields → SEMANTIC VIEW clauses needs live verification before implementation.

- **Conceptual mapping (expected, to be verified):**
  - `semantic_model.model` (ref/source target) → base table in `TABLES` clause.
  - `semantic_model.entities` → join keys in `RELATIONSHIPS` clause; sometimes surface in `DIMENSIONS` too.
  - `semantic_model.dimensions` → `DIMENSIONS` clause.
  - `semantic_model.measures` → `METRICS` clause with aggregation function.
- **Integration:** We do **not** emit raw CREATE SEMANTIC VIEW SQL directly. We delegate to the `dbt_semantic_view` package's custom materialisation, which takes a config block and builds the DDL. Our responsibility: produce a `.sql` file with `{{ config(materialized='semantic_view', tables=..., dimensions=..., metrics=...) }}` and the package handles DDL rendering.
- **Action item:** Phase-specific research to validate Snowflake DDL grammar and confirm the exact config field names the `dbt_semantic_view` package expects.

#### Databricks Metric View (DDL)

Confidence: **LOW** — design note states "in-progress Databricks package (stub until released)". Exact DDL syntax, availability status (private preview? public preview? GA?), and planned package name need live verification.

- **Placeholder strategy:** Our `DDLAdapter(Databricks)` emits a warning at generation time and writes either (a) a stub `.sql` with a TODO comment, or (b) a fallback plain view — **design-level open question in the codegen note**.
- **Forward-compatibility principle:** Isolate Databricks-specific logic in one adapter file so the eventual switch from stub → real package is a single-file edit.
- **Action item:** Re-research before a milestone that actually claims Databricks support. Until then, ship with "experimental/stub" labels.

#### DuckDB Semantic View (custom materialisation)

Confidence: **MEDIUM** — design notes state "Custom DuckDB extension (project-internal)". DuckDB has no native semantic view concept in current releases (as of training data); the plan is a custom materialisation that renders a conventional view plus generated metadata.

- **Integration:** Project-internal custom materialisation in the same repo (unlike Snowflake/Databricks, which depend on external packages). Scope kept as "clone of Snowflake's DDL and functionality" per the design note.

#### Snowflake-Labs `dbt_semantic_view` package

Confidence: **LOW** — referenced by repo name only; exact config surface and operational details not verified this pass.

- **Integration pattern:** Standard dbt package dependency in user's `packages.yml`. Our generated `sem__*.sql` files reference its custom materialisation via `{{ config(materialized='semantic_view', …) }}`.
- **Package boundary:** We do not import Python from it; we emit YAML/SQL that dbt executes. This keeps us engine-agnostic (Core and Fusion both run dbt packages identically in this respect).
- **Action item:** Phase-specific research to document config schema and any version constraints.

---

## Patterns to Follow

### Pattern 1: Subprocess boundary for licensed code

**What:** Wrap ELv2-licensed Fusion binary via `subprocess.run`; never import or link its code.
**When:** Any interaction with Fusion. Non-negotiable for license compliance.
**Example:**
```python
# adapter/fusion_runner.py
def parse_project(project_dir: Path) -> Path:
    result = subprocess.run(
        ["dbtf", "parse", "--project-dir", str(project_dir)],
        check=True, capture_output=True, text=True,
    )
    manifest_path = project_dir / "target" / "semantic_manifest.json"
    if not manifest_path.exists():
        raise ManifestNotFoundError(result.stderr)
    return manifest_path
```

### Pattern 2: Narrow public-API dependency on MetricFlow

**What:** Depend only on documented-or-public MetricFlow classes (`PydanticSemanticManifest`, `SemanticManifestLookup`, `MetricFlowEngine`, `MetricFlowQueryRequest`, `SqlClient` protocol).
**When:** All MetricFlow interactions.
**Pin:** Declare a specific compatible version range in `pyproject.toml`. CI runs fixture tests against pinned version on upgrade.

### Pattern 3: Sentinel prefix for ref() rewriting

**What:** Before building the engine, mutate `semantic_model.node_relation.relation_name` to a sentinel string (`__REF__.name`, `__SOURCE__.schema.name`, `__XREF__.project.model`, `__REFV__.model.v<N>`). After `explain()`, regex-substitute sentinels to Jinja in the rendered SQL.
**When:** Export SQL generation.
**Why:** `SqlTable.from_string` rejects >3 parts and enforces dot separation — sentinels respect this constraint. Renderer emits verbatim, so sentinels survive to the output. Substitutions are unambiguous because no real warehouse table can share the `__REF__`/`__SOURCE__` prefix.
**Example:**
```python
# Pre-engine mutation (SemanticModelMutator)
for sm in manifest.semantic_models:
    node = node_map[sm.name]  # from manifest.json depends_on
    sm.node_relation.relation_name = to_sentinel(node)  # e.g. "__REF__.stg_orders"

# Post-render (RefRewriter)
sql = re.sub(r"\b__REF__\.(\w+)\b",           r"{{ ref('\1') }}",          sql)
sql = re.sub(r"\b__SOURCE__\.(\w+)\.(\w+)\b", r"{{ source('\1', '\2') }}", sql)
sql = re.sub(r"\b__XREF__\.(\w+)\.(\w+)\b",   r"{{ ref('\1', '\2') }}",    sql)
sql = re.sub(r"\b__REFV__\.(\w+)\.v(\d+)\b",  r"{{ ref('\1', v=\2) }}",    sql)
```

### Pattern 4: Adapter dispatch for DDL

**What:** One `DDLAdapter` per warehouse, registered in a dialect → builder map. Core generator is warehouse-agnostic.
**When:** Any code that renders DDL or Snowflake-specific / Databricks-specific config.
**Benefit:** Adding a warehouse = adding an adapter. Stub adapters fail loud with a clear message.

### Pattern 5: Codegen over dbt-plugins API

**What:** Generate static `.sql` files to disk rather than injecting synthetic nodes via `dbt.plugins.get_nodes()`.
**When:** Always. This is the core strategic choice.
**Why:** `dbt.plugins` is undocumented, has one known real-world user (`dbt-loom`), and has no Fusion equivalent. Static files are:
- Engine-agnostic (Core and Fusion both parse `.sql` files identically).
- Transparent (readable by humans, `dbt ls`, lineage tools).
- Stateless (no plugin registration, no runtime surprises).

### Pattern 6: Idempotent file writes

**What:** `FileWriter` compares new content to existing file; writes only if different.
**When:** All output writes.
**Why:** Pre-commit hook triggers repeatedly. Idempotent writes prevent spurious git diffs and pre-commit loops.

### Pattern 7: Two-tier parser (Fusion path + fast path)

**What:** Default to Fusion binary. Expose a `--fast` flag that skips Fusion and uses `dir_to_model` directly, for legacy-spec-only projects that want to avoid the Fusion install.
**When:** Useful for legacy-spec projects and CI environments where installing Fusion is awkward. Both paths produce the same downstream object (`PydanticSemanticManifest`), so all components below the parser are unaffected.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Subclassing MetricFlow frozen dataclasses

**What:** Subclassing `SqlTable` or overriding `visit_table_node` to inject Jinja.
**Why bad:** `SqlTable` is a frozen dataclass constructed at many internal call sites (`dataflow_to_subquery.py:320, 1625, 2171, 2260, 2352, 2396`; `dataflow_to_cte.py:72`). Subclassing breaks on any MetricFlow internal refactor.
**Instead:** Sentinel prefix approach (Pattern 3) — narrow dependency on two public surfaces only.

### Anti-Pattern 2: Injecting Jinja directly into `relation_name`

**What:** Setting `relation_name = "{{ ref('stg_orders') }}"`.
**Why bad:** `SqlTable.from_string` enforces 2- or 3-part dot splits. Jinja fails validation immediately.
**Instead:** Sentinel form (2–3 parts, dot-separated).

### Anti-Pattern 3: Name-based heuristic post-processing

**What:** Matching raw table names in rendered SQL against the dbt model registry to guess ref calls.
**Why bad:** Breaks when a real warehouse table shares a name with a dbt model. Produces silent miscompilation.
**Instead:** Manifest-driven sentinel substitution with an explicit map.

### Anti-Pattern 4: Embedding Fusion crates via PyO3

**What:** Vendor `dbt-parser` / `dbt-schemas` crates and expose them through PyO3 bindings.
**Why bad:** (a) ELv2 license propagates; (b) no upstream PyO3 bindings exist; (c) crates aren't published to crates.io; (d) ongoing maintenance burden tracking Fusion internals; (e) Fusion doesn't have the MetricFlow SQL compiler — you'd still need Python for SQL generation.
**Instead:** Subprocess the Fusion binary.

### Anti-Pattern 5: Relying on `dbt.plugins.get_nodes()`

**What:** Injecting semantic-view/export models as synthetic dbt nodes at parse time.
**Why bad:** (a) Undocumented API, one known user; (b) no Fusion equivalent — the tool becomes Core-only; (c) invisible nodes are debugging hostile.
**Instead:** Written-to-disk files (Pattern 5).

### Anti-Pattern 6: Custom materialisation for semantic views in *this* package

**What:** Write our own `materialized='semantic_view'` implementation.
**Why bad:** Snowflake-Labs already has one. Adding a wrapper layer duplicates logic and couples us to Snowflake internals we don't control.
**Instead:** Generate `.sql` files that call Snowflake-Labs's materialisation directly. DuckDB is the sole exception — we implement that ourselves because no upstream exists.

### Anti-Pattern 7: Silent manifest schema drift

**What:** Assume Fusion's `semantic_manifest.json` stays wire-compatible with MetricFlow's `PydanticSemanticManifest` forever.
**Why bad:** Two independent codebases. Pydantic validation failures surface late, often in user projects.
**Instead:** CI fixture test: load a representative Fusion-emitted manifest into `PydanticSemanticManifest.parse_file` on every upgrade of either dependency. Pin compatible version ranges.

---

## Scalability Considerations

The workload is developer-time generation, not runtime user traffic. "Scale" here means project size and regeneration frequency.

| Concern | 10 semantic models / 50 exports | 100 / 500 | 500 / 2000 |
|---------|--------------------------------|-----------|------------|
| Full regeneration time | <2s (Fusion parse) + <5s (engine setup + explain loop) | ~5s + ~30s | ~15s + ~2min |
| Fusion subprocess overhead | Fixed ~1s cold start | Amortised | Amortised |
| `MetricFlowEngine.explain()` per export | ~100ms/export (training-data estimate, **LOW** confidence) | Same | Same |
| Output file count | 60 files | 600 files | 2500 files |
| Pre-commit hook UX | Instantaneous | Noticeable but tolerable | Slow — diff-based regeneration becomes attractive |

**Implications:**
- Up to ~100 exports: simple full-regeneration is fine.
- Beyond ~500 exports: consider caching the `PydanticSemanticManifest` between runs and diffing which saved queries actually changed.
- Engine construction cost is one-time per run; amortise over all exports in a single process.

**Scaling levers (deferred until needed):**
1. Parallelise the export generation loop (each `explain()` call is independent).
2. Cache `semantic_manifest.json` modification time; skip Fusion if unchanged.
3. Diff-mode: re-run only affected saved queries based on YAML diff.

---

## Build Order Implications

Dependencies between components dictate a natural phase ordering. Suggested build order (to be confirmed by the roadmap orchestrator):

1. **Foundation — parsing pipeline**
   Build: `ProjectResolver`, `ManifestLoader` (Fusion subprocess + fast-path), `FastPathParser`, `RefResolver`.
   Validation: round-trip a fixture `semantic_manifest.json` → `PydanticSemanticManifest` → assert equality on key fields.
   Why first: everything downstream needs a loaded manifest.

2. **Export SQL generation — the highest-risk area**
   Build: `StubSqlClient`, `SemanticModelMutator`, `ExportSqlBuilder`, `RefRewriter`.
   Validation: fixture project with one saved query with one export → generated SQL compiles under `dbt parse` on both Core and Fusion.
   Why second: this is where the novel sentinel-prefix technique lives. Confidence needs to be earned early — if MetricFlow's internal contracts move, we discover it here.

3. **File output + CLI**
   Build: `FileWriter`, `CLI Entrypoint`, `_semantic_views.yml` schema-file renderer.
   Validation: end-to-end: `dbt-s-layer generate` on fixture project produces buildable dbt project.
   Why third: turns the pipeline into a usable tool; enables self-dogfooding of all future adapter work.

4. **Snowflake semantic view DDL adapter**
   Build: `SemanticViewDDLBuilder`, `DDLAdapter(Snowflake)`, integration with `dbt_semantic_view` package config.
   Validation: generated `sem__*.sql` builds under `dbt run` against a Snowflake warehouse.
   Why fourth: primary warehouse target; most mature external integration. Requires phase-specific research to validate DDL grammar and package config.

5. **Pre-commit hook**
   Build: `PrecommitHook`, idempotency tests.
   Validation: change semantic YAML → pre-commit regenerates → git status shows expected diff, no infinite loop.
   Why fifth: developer ergonomics layer on top of a working CLI.

6. **Databricks adapter (stub)**
   Build: `DDLAdapter(Databricks)` with warning-and-skip behaviour.
   Why sixth: explicit placeholder. Re-scope to "real implementation" when the upstream package releases.

7. **DuckDB adapter (custom materialisation)**
   Build: custom materialisation in project, `DDLAdapter(DuckDB)`.
   Validation: generated `sem__*.sql` runs under DuckDB.
   Why last of v1: no external blocker but lowest user priority of the three warehouses.

**Critical build-order constraint:** Steps 1 → 2 → 3 are strictly sequential (each depends on the previous). Steps 4, 6, 7 can parallelise once step 3 is done. Step 5 (pre-commit) can land any time after step 3.

---

## Sources

- `_notes/semantic-manifest-loading.md` (internal design note, 2026-04) — primary source for Fusion CLI integration, MetricFlow public/private surfaces, sentinel-prefix approach, manifest-drift risks. **HIGH confidence** on internals (cites specific file:line in `_metricflow/` and `_fusion/` source trees).
- `_notes/dbt-semantic-views-codegen.md` (internal design note) — primary source for adapter-dispatch strategy, DAG shape, codegen-over-plugin rationale. **HIGH confidence** on architecture, **MEDIUM** on external package specifics (names only).
- `.planning/PROJECT.md` — scope, constraints, key decisions. **HIGH confidence**.
- Training-data awareness of Snowflake `CREATE SEMANTIC VIEW`, Databricks metric views, dbt Fusion CLI surface, `dbt_semantic_view` package. **LOW confidence** — external sources (WebFetch, WebSearch, Context7) were not accessible this pass. Phase-specific research required before implementation work on adapters (Snowflake, Databricks) and the `dbt_semantic_view` package integration.

### Flagged for phase-specific re-research

- Snowflake `CREATE SEMANTIC VIEW` DDL grammar and `dbt_semantic_view` config schema — before Snowflake adapter phase.
- Databricks metric view DDL, availability, and upstream package release status — before Databricks adapter phase.
- `dbtf parse` exact CLI flags in current Fusion release — before ManifestLoader completion.
- MetricFlow version compatibility matrix for the specific `MetricFlowEngine.explain()` return shape — before ExportSqlBuilder completion.
