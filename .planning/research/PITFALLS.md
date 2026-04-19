# Domain Pitfalls

**Domain:** Python code-generation tooling that bridges open-source dbt (Core + Fusion) with dbt Cloud's semantic-layer features and warehouse-native semantic objects (Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB semantic views).
**Researched:** 2026-04-18

Confidence note: the project brief and two design notes in `_notes/` already contain specific, source-code-grounded investigation of many pitfalls (with file/line references into `_metricflow`, `_fusion`, and Snowflake-Labs packages). WebSearch / Context7 were unavailable during this research pass, so public/third-party verification beyond those notes is marked LOW confidence and flagged for re-verification. The pitfalls below synthesise the design notes and add domain pitfalls that are typical for this class of tool (codegen + external binary + external evolving spec + pre-commit hook + multi-dialect).

---

## Critical Pitfalls

Mistakes that cause rewrites, user-visible data incorrectness, or fundamental re-architecture.

### Pitfall 1: Treating `MetricFlowEngine.explain()` as a stable public API
**What goes wrong:** We build around `engine.explain().sql_statement.sql` and `SqlTable.from_string` + `visit_table_node` as load-bearing integration points (per `_notes/semantic-manifest-loading.md`). These surfaces are **public-ish** in the `metricflow` Apache-2.0 package but are not covered by a formal semver contract. A minor version bump can rename a method, change return shape, or restructure the SQL plan tree.
**Why it happens:** `metricflow` is primarily consumed inside dbt Cloud; the open-source distribution is a byproduct, not a product. Dbt Labs have in the past broken internal shape of the engine between releases without incrementing a major version.
**Consequences:** Silent SQL drift (the same saved query suddenly renders with different joins), `AttributeError` at import time, or — worst case — SQL that runs but returns wrong numbers because the rewrite regex no longer matches the renderer output.
**Prevention:**
- Pin `metricflow` to an exact version; upgrade deliberately and only with a fixture-diff test suite that compares rendered SQL for a golden set of saved queries.
- Ship a thin adapter module (`metricflow_adapter.py`) that is the *only* place which touches `MetricFlowEngine`, `SqlClient`, `SqlTable`, and plan-renderer internals. Any breakage is isolated there.
- Add a smoke test that asserts (a) `engine.explain(...).sql_statement.sql` still returns a string, (b) `SqlTable.from_string("__REF__.x")` and `SqlTable.from_string("__SOURCE__.x.y")` still parse, and (c) the renderer still emits the sentinel verbatim. Run on every metricflow upgrade.
- Snapshot-test the rendered SQL for each fixture saved query; gate upgrades on a human-reviewed diff.
**Detection:** CI failure on the metricflow pin bump; snapshot-SQL diffs on golden fixtures; `hasattr(engine, "explain")` guard.
**Phase mapping:** Address in the phase that wires MetricFlow into the generator (export SQL rendering). Golden-fixture infra is a prerequisite before touching any dialect output.

### Pitfall 2: Sentinel-prefix rewrite produces wrong SQL for edge-case table layouts
**What goes wrong:** The sentinel approach (`__REF__.x`, `__SOURCE__.x.y`, `__XREF__.a.b`, `__REFV__.m.v2`) relies on:
1. `SqlTable.from_string` only accepting 2- or 3-part identifiers.
2. `visit_table_node` emitting `node.sql_table.sql` verbatim with no quoting, aliasing, or case-folding.
3. Every physical table reference in the rendered SQL originating at `convert_semantic_model.py:514` or `:591` (time spines).

Any of those assumptions breaking (e.g. a future `visit_table_node` adds dialect quoting like `"__REF__"."x"`, or a future metricflow version introduces a 4-part identifier for Databricks three-level namespace `catalog.schema.table` or Snowflake's `db.schema.table` being misparsed into the `__SOURCE__` regex) will produce either (a) un-rewritten `__REF__` tokens in the final SQL or (b) `{{ ref(...) }}` calls with the wrong arguments.
**Why it happens:** The sentinel trick is clever, and the design note explicitly says it depends on "two narrow public surfaces." It trades breadth of coverage for implementation simplicity. Edge cases accumulate.
**Consequences:**
- `__REF__`/`__SOURCE__` tokens leaking into generated `.sql` files and exploding at dbt parse time ("source not found" or SQL syntax error on the underscore prefix).
- Worse: post-rewrite SQL that references the *wrong* ref (e.g. a real user table literally named `__REF__` or a 3-part Snowflake identifier `analytics.public.orders` misread as `__SOURCE__.analytics.public`).
- Quoted identifier mismatches on Snowflake (case sensitivity of unquoted identifiers vs. quoted).
**Prevention:**
- **Pre-flight scan** before generation: walk the manifest for semantic models whose `relation_name` already contains `__REF__`, `__SOURCE__`, `__XREF__`, `__REFV__` (or user ref/source names that match), and error out with a clear message. (The design note flags this but does not require it — make it mandatory in phase 1.)
- **Post-flight scan** after generation: refuse to write any `.sql` file that still contains a sentinel token. Fail loudly, not silently.
- **Renderer-output test**: assert that `visit_table_node` still emits sentinels unmodified for every supported metricflow version. Canary test, small and fast.
- Document reserved prefixes in user-facing docs and CLI error messages.
- For Databricks three-level namespace (`catalog.schema.table`), investigate whether metricflow emits 3-part identifiers and, if so, ensure our sentinel regex handles both "my_catalog.schema.table" (a user source) and "__SOURCE__.schema.table" (our sentinel) unambiguously — the length-3 case is currently the most load-bearing.
- Use a sentinel prefix unlikely to collide: quad underscore `____REF____` or an explicit tag like `__DBTSLAYER_REF_<uuid>__` if risk assessment suggests it.
**Detection:** Post-flight grep for sentinel tokens; snapshot-fixture regression tests for warehouse-dialect output; a "hostile fixture" in the test suite with a semantic model literally named `__REF__`.
**Phase mapping:** Address in the first phase that wires the rewrite. Hostile fixtures must exist before the rewrite is considered done.

### Pitfall 3: `semantic_manifest.json` schema drift between dbt Fusion (Rust) and Python `metricflow` (Pydantic)
**What goes wrong:** Fusion serialises `SemanticManifest` from a Rust struct defined at `_fusion/crates/dbt-schemas/src/schemas/semantic_layer/semantic_manifest.rs`. We deserialise with `PydanticSemanticManifest` from the `metricflow_semantic_interfaces` package. Both derive from the same spec, but they're independent codebases. Any new optional field added on one side, any type-tightening on the other, any enum-value casing difference, or any rename will break loading.
**Why it happens:** Two-codebase parallel evolution. The design note explicitly calls out `export.config.export_as` enum values (`TABLE`/`VIEW`) and `saved-query parameter coverage` as unverified. Expect similar drift across `where` filters, custom granularities, time spines, and metric type-params (especially now that the new spec flattens them).
**Consequences:**
- Hard failure at `PydanticSemanticManifest.parse_file(path)` with a Pydantic `ValidationError` — tool breaks for any user on a Fusion version we haven't tested.
- Silent data loss if a field is optional on the Pydantic side and Fusion starts emitting something we ignore.
- Unit-test suite passes but real user projects fail because our fixture was generated from an older Fusion.
**Prevention:**
- **Version matrix in CI**: test against a declared compatibility matrix `{fusion_version} × {metricflow_version}`. Each cell runs the full fixture suite end-to-end. Publish the supported matrix in our README; fail fast at runtime if the user's Fusion is outside it.
- **Fusion binary detection** at startup: probe `dbtf --version`, compare against matrix, warn or error.
- **Fixture fixtures**: for each supported Fusion version, commit a pre-generated `semantic_manifest.json` fixture so unit tests don't require Fusion to run.
- **Schema-diff checker**: a CI job that runs `dbtf parse` against a canonical project with a new Fusion version and diffs the generated manifest against the previous one. Human-reviewed.
- Ship a `dbt-s-layer doctor` command that validates the user's Fusion + metricflow combination.
**Detection:** Pydantic `ValidationError` at load time; CI matrix failures; user bug reports mentioning "Fusion version X".
**Phase mapping:** Phase that integrates Fusion subprocess. Compatibility matrix must exist before the first public release.

### Pitfall 4: Snowflake `SEMANTIC VIEW` DDL is not mature; Databricks metric views are not GA
**What goes wrong:** The design explicitly delegates Snowflake materialisation to the Snowflake-Labs `dbt_semantic_view` package and flags Databricks as "stub, awaiting upstream package." There is substantial risk that:
- Snowflake `SEMANTIC VIEW` syntax changes between preview and GA (has happened with other Snowflake features, e.g. `DYNAMIC TABLE`).
- `dbt_semantic_view` package is not yet semver-stable — upstream breaking changes land without major version bumps.
- Databricks metric views never ship, or ship with DDL too different from Snowflake to keep the "thin translation layer" claim.
- Feature parity differences: Snowflake may support window measures that Databricks doesn't (or vice versa), and mapping dbt semantic YAML to both is not a simple 1:1.
**Why it happens:** Warehouse-native semantic objects are a 2025–2026 feature; they're all in preview or early GA. Vendor roadmaps are marketing-driven and slip.
**Consequences:**
- Generated DDL that is syntactically valid today but fails on tomorrow's Snowflake deploy.
- Users on Databricks get a stub that silently emits a plain SQL view, with no notice that semantics are lost — dashboard BI integrations don't know it's "semantic" and users lose advertised functionality.
- Feature-matrix confusion: a metric that renders on Snowflake fails on Databricks, and error messages blame the tool rather than the warehouse.
**Prevention:**
- Maintain an **explicit support matrix** per warehouse per feature (ratio metrics, cumulative metrics, time spines, custom granularities, derived metrics). Publish it. Refuse to generate output that relies on unsupported features, with an actionable error.
- **Dialect feature-detection layer**: a per-adapter capability map that the generator consults before emitting DDL. Adding a feature requires declaring its support status per adapter.
- For Databricks specifically: default behaviour is to **emit no file + print a clear warning** (not a silent fallback view). This avoids false-positive "success" outputs. Add a future flag `--databricks-fallback=view` only once the UX is understood.
- Version-pin `dbt_semantic_view` and treat upgrades as breaking by default.
- Integration tests against live Snowflake/Databricks/DuckDB in CI (at least one warehouse per PR; all three on release). Use ephemeral accounts or free tiers.
- Follow Snowflake/Databricks release notes RSS; have a named owner for "warehouse semantic object compatibility."
**Detection:** Release-gated integration tests; user-reported DDL failures; matrix diffs.
**Phase mapping:** Phase that adds each warehouse target. Capability-matrix infra is a prerequisite to the second warehouse, not something to bolt on later.

### Pitfall 5: Pre-commit hook causes developer-workflow pain that kills adoption
**What goes wrong:** Pre-commit hooks that are slow, non-deterministic, or that modify files unexpectedly will be disabled by users. Specific failure modes:
- Regeneration takes > 2 s on large projects → users bypass with `git commit --no-verify`.
- Hook modifies files that the user didn't stage → `git add` / `git commit` cycle confusion.
- Hook writes files that differ from CI's version of the same hook → "works on my machine" drift.
- Partial YAML edits (e.g. adding a new semantic model but not yet its measures) trigger full regeneration and emit broken SQL that then gets committed.
- Hook invokes `dbtf parse`, which requires `profiles.yml` and a reachable warehouse connection — running a pre-commit that hits a VPN-gated Snowflake is a bad UX.
**Why it happens:** Pre-commit hooks are the right UX for "keep derived files in sync" but the failure modes are legendary (see how many teams disable `black`, `isort`, or `prettier` hooks).
**Consequences:** Users opt out; generated files drift; the tool's "single source of truth" promise is broken.
**Prevention:**
- **Performance budget**: regeneration on a 100-semantic-model project must finish in < 5 s. Profile early; consider manifest-hash caching so Fusion re-parse only runs when YAML actually changed.
- **Diff-only regen**: if only one semantic model's YAML changed, regenerate only its `sem__*.sql`; don't touch unrelated files. (The design notes flag this as "open question." Answer: diff-based is required for pre-commit UX even if full regen is acceptable for CLI-triggered runs.)
- **Stub profile**: detect whether `dbtf parse` can use a stub/dummy `profiles.yml` and document exactly what the user needs. If a real warehouse connection is mandatory, the hook cannot run in restricted environments — document that honestly and provide a `--skip-regen` escape hatch.
- **Determinism**: identical YAML + tool version must produce byte-identical SQL. Sort dict keys; avoid Python-dict-order-dependent rendering; stable line-ending policy; stable floating-point format; stable timestamp-free headers.
- **Hook must self-check version**: if the installed `dbt-s-layer` is older than the YAML's declared version, fail with "upgrade the tool" rather than generating stale SQL.
- **Validation-only mode** (`dbt-s-layer check`): CI runs this; it re-generates in a temp dir and fails if the commit would drift. Local hook is `generate` + auto-stage. This mirrors the `black --check` / `terraform fmt -check` pattern.
- **Partial-edit safety**: run the semantic-manifest validator (Fusion already does this) and refuse to write output if any semantic model fails validation. Don't emit broken SQL.
- **Multi-file atomicity**: write all files to a staging dir, validate they all parse as SQL, then move into place atomically. No half-updated states on disk.
**Detection:** User bug reports mentioning slowness or `--no-verify`; CI drift between "local-generated" and "CI-generated" files; flaky pre-commit runs.
**Phase mapping:** Phase that ships the pre-commit hook. Performance and determinism are MVP-blocking; advanced diff-based regen can ship in a follow-up phase.

### Pitfall 6: ELv2 licensing contamination from dbt Fusion
**What goes wrong:** dbt Fusion's parser/schemas crates (`crates/dbt-parser`, `crates/dbt-schemas`) are ELv2-licensed, which prohibits offering the software as a hosted service competitive with dbt Cloud. If we accidentally embed or vendor any ELv2-licensed code into `dbt-s-layer`, our own license must comply — killing Apache-2.0 / MIT distribution and any downstream use-case that includes "run this as a service."
**Why it happens:** Vendor-first-and-ask-questions-later is a common developer habit. Easy to fall into while trying to avoid the subprocess dependency.
**Consequences:** License violation; cannot be bundled by a dbt-alternative SaaS vendor; GitHub sponsors / commercial support model becomes legally ambiguous.
**Prevention:**
- Policy: **no vendored Fusion code, no PyO3 bindings to Fusion crates, no copying schema types from Fusion repo into ours.** The only interaction is by subprocess (`dbtf parse`) and deserialising the JSON it emits.
- License-scan CI: add a tool like `scancode-toolkit` or `pip-licenses` to the release pipeline; gate publish on "no ELv2 components in wheel."
- For deserialisation, use the Apache-2.0 `metricflow_semantic_interfaces.PydanticSemanticManifest` (derived from OSI spec, not from Fusion's Rust struct). Do NOT copy Fusion's Rust struct field-by-field into a Python class.
- Third-party license review before the first public release.
**Detection:** License scanner CI step; external audit if taking commercial funding.
**Phase mapping:** Phase 0 / governance. Policy must exist before first code is written. Audit runs before first public release.

---

## Moderate Pitfalls

Mistakes that cause user friction, support burden, or surface bugs — but don't force rewrites.

### Pitfall 7: `dbtf parse` requires `profiles.yml` even for metadata-only operations
**What goes wrong:** Fusion's `parse` subcommand requires a valid `profiles.yml` to be present and reachable, even though parsing doesn't hit the warehouse. Users running `dbt-s-layer` in CI, in Docker sandboxes, or on machines that can't see the production Snowflake will hit credential / connection errors.
**Prevention:**
- Research whether a stub profile (e.g. SQLite, DuckDB-in-memory) is acceptable to `dbtf parse` — if yes, generate and inject one automatically.
- Document the requirement up front; provide a `dbt-s-layer init` command that scaffolds a stub profile.
- If Fusion will not accept a stub, build a `--offline` mode that caches the last successful `semantic_manifest.json` and re-uses it when `dbtf parse` cannot run. Log the fallback clearly.
- Add this to `dbt-s-layer doctor`.
**Phase mapping:** Phase that first invokes Fusion subprocess.

### Pitfall 8: Legacy spec fast-path diverges from Fusion path over time
**What goes wrong:** The design notes allow a fast-path: for projects known to be on the legacy YAML spec, skip Fusion and call `parse_directory_of_yaml_files_to_semantic_manifest` directly. This is pragmatic but doubles maintenance — the two paths produce *nominally* the same `PydanticSemanticManifest`, but subtle differences accumulate (default values, normalisation rules, validator side effects).
**Prevention:**
- Run every fixture through **both paths in CI** and diff the resulting manifest objects. Any divergence fails CI.
- Document the fast-path as "best-effort, for advanced users on legacy-spec-only projects"; default remains the Fusion path.
- Plan to deprecate the fast-path on a timeline: once the new YAML spec is > 50% of user base (or at v2.0), remove it. Prevents long-term dual maintenance.
- Detect spec version automatically; don't let users pick the wrong path.
**Phase mapping:** Phase that implements the fast-path. Divergence-detection test is a prerequisite to enabling it.

### Pitfall 9: `SqlClient` stub fails a future interface expansion
**What goes wrong:** We pass a stub `SqlClient` to `MetricFlowEngine` so we can call `.explain()` without executing SQL. If metricflow adds a new method to the `SqlClient` protocol, our stub breaks with `NotImplementedError` or `AttributeError`.
**Prevention:**
- Make the stub a subclass of `abc.ABC` that implements every abstract method with a loud `raise NotImplementedError("dbt-s-layer SqlClient is render-only; method {name} was called unexpectedly")`.
- Pin metricflow version (same requirement as Pitfall 1).
- Add a `__init_subclass__` or interface-verification test that asserts our stub implements every `@abstractmethod` on `SqlClient`.
**Phase mapping:** Phase that wires MetricFlow.

### Pitfall 10: Time-spine rewrite forgotten
**What goes wrong:** Design note explicitly calls out `time_spine.node_relation.relation_name` (`convert_semantic_model.py:591`) as going through the same code path — we must apply the sentinel to time spines too. Easy to implement the `semantic_models` iteration and forget time spines.
**Prevention:**
- Fixture project with an explicit time-spine model defined as a dbt `ref()`. Rendered SQL must contain `{{ ref('time_spine_day') }}`.
- Code-review checklist item for the rewrite implementer.
**Phase mapping:** Phase that implements ref-rewrite.

### Pitfall 11: Cross-project refs and versioned refs are uncommon but real
**What goes wrong:** The design note lists `__XREF__` (cross-project refs) and `__REFV__` (versioned refs) as sentinel cases. These are easy to defer ("edge cases, few users use cross-project refs"), but the minority of users who rely on them are typically enterprise customers who churn loudly when things break.
**Prevention:**
- Implement all four sentinel patterns (ref, source, xref, versioned) in phase 1, even if fixtures for xref/versioned are minimal.
- Test coverage for xref/versioned is a required gate, not a nice-to-have.
**Phase mapping:** Phase that implements ref-rewrite — ship all four, not just two.

### Pitfall 12: Identifier quoting and case sensitivity across warehouses
**What goes wrong:** Snowflake folds unquoted identifiers to uppercase; Databricks uses lowercase; DuckDB is case-insensitive but case-preserving. If we emit `__REF__.my_model` and metricflow/rendering does any quoting, the final `{{ ref('my_model') }}` might resolve to the wrong table after Snowflake's uppercase folding. Design note states `SqlTable.sql` does NO quoting — but this is a metricflow-version-dependent behaviour and could change.
**Prevention:**
- Snapshot-test the final rendered SQL for each dialect; verify identifier case is preserved and Jinja tags render correctly.
- If metricflow starts quoting, we must strip quotes from the sentinel token before regex-substitution. Write the regex to tolerate optional quoting: `"?__REF__"?\."?(\w+)"?`.
**Phase mapping:** Phase that adds each dialect's snapshot tests.

### Pitfall 13: Generated-file conflict with hand-authored models
**What goes wrong:** Users have existing `models/` directories with names that collide with generated `sem__*` / `exp__*` files. Users accidentally hand-edit a generated file; next regen overwrites their edits.
**Prevention:**
- **Banner comment at the top of every generated file**: `-- THIS FILE IS GENERATED BY dbt-s-layer. DO NOT EDIT. Edit the semantic YAML instead.` with a hash of the YAML source. Pre-commit hook refuses to commit if the hash mismatches (detects hand edits).
- Namespace generated files in `models/semantic/` (default) — separate directory to make `.gitignore` / `dbt --select path:` patterns easy.
- `dbt-s-layer generate --check` mode for CI.
**Phase mapping:** Phase that implements the file writer.

### Pitfall 14: `dbt_project.yml` / `packages.yml` coupling breaks
**What goes wrong:** For Snowflake, we rely on users installing the `dbt_semantic_view` package (Snowflake-Labs). If the user doesn't add it to `packages.yml`, the generated `{{ config(materialized='semantic_view') }}` fails with "materialization not found." Confusing UX if it happens at `dbt run` rather than at our tool's validation step.
**Prevention:**
- `dbt-s-layer doctor` command validates `packages.yml` contains the right dependencies for the user's target warehouse.
- At generation time, scan `packages.yml` and emit a clear error if the needed package is missing, before writing any files.
**Phase mapping:** Phase that integrates Snowflake target.

### Pitfall 15: Enum case-sensitivity drift (`TABLE` vs `table`)
**What goes wrong:** Design note flags that `export.config.export_as` is an enum (`TABLE` / `VIEW`) in metricflow. Fusion may serialise it as `"table"`, `"TABLE"`, `"Table"`, or as an integer. If we do string comparisons the naïve way, it breaks.
**Prevention:**
- Always compare enums case-insensitively AND use metricflow's own Pydantic enum rather than raw strings. Pydantic's enum validator will catch this at load time — if it doesn't, add explicit tests for each enum value against each Fusion version.
- Fixture manifests covering `TABLE`, `VIEW`, `CACHE` (if metricflow supports it), with round-trip verification.
**Phase mapping:** Phase that implements export-model emission.

### Pitfall 16: `where` filters and custom granularities don't round-trip
**What goes wrong:** Design note explicitly says "Confirm that all fields a saved query can reference (`where`, custom granularities, time spines) round-trip correctly through the Fusion-emitted manifest into metricflow's engine." If any of these is dropped, saved queries render incorrectly — usually with silently too-many rows (missing `WHERE`) or wrong time buckets (missing custom granularity).
**Prevention:**
- Fixture saved queries exercising every combination: `where` filter, `where` with Jinja DSL, custom granularity, time-dimension granularity, metric-time spine, aliased metrics, `limit`, `order_by`.
- Snapshot tests for the rendered SQL of each.
- Do NOT ship a warehouse target until every one of these round-trips correctly.
**Phase mapping:** Phase that generates exports — test suite is the gate.

### Pitfall 17: Snowflake `SEMANTIC VIEW` feature gaps vs. dbt semantic YAML
**What goes wrong:** dbt's semantic YAML supports features (derived metrics, conversion metrics, cumulative metrics, ratio metrics, fill-null-with-zero, offset windows) that may or may not translate to a single Snowflake `CREATE SEMANTIC VIEW` statement. Translation bugs produce "works on Cloud, doesn't work on Core."
**Prevention:**
- Per-feature capability matrix (already listed under Pitfall 4). For each dbt semantic YAML construct, document what Snowflake/Databricks/DuckDB natively support and what we synthesise in export SQL instead.
- Split generation responsibility: features that warehouses can't express natively live only in `exp__*` models; `sem__*` files declare only the intersection.
**Phase mapping:** Phase that adds each warehouse. Matrix evolves over time.

---

## Minor Pitfalls

Mistakes that cause confusion or polish issues, not outages.

### Pitfall 18: `dbt-autofix` migrates user YAML but our fast-path doesn't track it
**What goes wrong:** User runs `dbt-autofix` to migrate legacy → new spec. Our legacy fast-path now fails because the YAML is in the new spec, but error messages refer to "legacy parser" internals.
**Prevention:** Auto-detect spec version from YAML shape; don't rely on user config to pick a path.

### Pitfall 19: `semantic_manifest.json` path isn't always `target/semantic_manifest.json`
**What goes wrong:** `dbt_project.yml` allows `target-path:` override. Hard-coding `target/semantic_manifest.json` breaks for users with custom target directories.
**Prevention:** Read `target-path` from `dbt_project.yml`; fall back to `target/`.

### Pitfall 20: `dbtf` binary name not always `dbtf`
**What goes wrong:** Some users alias Fusion to `dbt`, or install via Homebrew / installer that puts it elsewhere on `PATH`. Hard-coding `dbtf` breaks.
**Prevention:** Configurable via env var `DBT_S_LAYER_FUSION_BIN` (default `dbtf`); fall back to searching for the first of `[dbtf, dbt]` that has `parse` subcommand and reports a Fusion-version string.

### Pitfall 21: Python version compatibility
**What goes wrong:** `metricflow` drops Python 3.9 support without a major version bump; our minimum Python version silently becomes 3.10 and breaks installs on older CI.
**Prevention:** Declare Python version range explicitly in `pyproject.toml`; run CI matrix across all declared versions; follow SPEC 0 for Python version policy.

### Pitfall 22: Windows path handling
**What goes wrong:** `subprocess.run(["dbtf", "parse", "--project-dir", path])` where `path` is a Windows `WindowsPath` works mostly, but `PATH` resolution, quoting, and line endings (CRLF in generated `.sql` files) can bite.
**Prevention:** Use `pathlib` everywhere; explicit `newline='\n'` when writing files; test on Windows in CI (GitHub Actions `windows-latest`).

### Pitfall 23: dbt-Core version matrix
**What goes wrong:** dbt Core 1.7 introduced semantic models GA. Between 1.7 and 1.12 (new spec), and between 1.12 and Core 2.x, the manifest shape has evolved. Tests against only one Core version miss regressions.
**Prevention:** Declare a minimum version (≥ 1.7) and a tested version range; CI matrix; `dbt-s-layer doctor` reports compatibility status.

### Pitfall 24: Emoji and Unicode in generated SQL comments
**What goes wrong:** User puts emoji or non-ASCII chars in semantic-model descriptions, we render them into SQL comments, and some warehouse dialect (Snowflake historically, less so now) chokes on non-UTF-8.
**Prevention:** Always write UTF-8 with explicit encoding; escape non-printable chars in SQL comments; test fixture includes non-ASCII description.

### Pitfall 25: Logging / error messages leak sensitive information
**What goes wrong:** `dbtf parse` output may include warehouse credentials, connection strings, full SQL paths. Capturing and logging stderr naïvely exposes them.
**Prevention:** Filter known-sensitive patterns before logging; document that logs should not be pasted publicly.

### Pitfall 26: Incremental exports as future scope attract premature feature requests
**What goes wrong:** Users ask for `materialized='incremental'` in the first week. We either say "no" (adoption hit) or build it fast and wrong.
**Prevention:** Document up front that incrementals are a follow-up milestone; propose the time-dimension-based predicate pattern from design notes; park the issue until a second phase.

### Pitfall 27: Generated-file re-ordering causes git churn
**What goes wrong:** Python dict order is insertion-ordered, but iterating `manifest.semantic_models` might yield a different order across runs depending on Fusion's emission order or Pydantic's internal handling. Every regen produces a no-op diff with 200 lines of re-ordered model content.
**Prevention:** Sort semantic_models/metrics/saved_queries by name before generating; stable column order within each file; stable indentation and formatting.

### Pitfall 28: Generated YAML (`_semantic_views.yml`) and `.sql` get out of sync
**What goes wrong:** We write both `sem__orders.sql` and an entry in `_semantic_views.yml`. A partial regen (or a crash mid-write) leaves one updated and the other stale. dbt then fails with "model not found in schema file" or vice versa.
**Prevention:** Write all generated files to a staging directory; only `os.replace()` into `models/semantic/` after all are valid. Either all or none.

### Pitfall 29: DuckDB semantic view custom materialisation diverges from Snowflake
**What goes wrong:** DuckDB extension is described as "a clone of Snowflake's DDL and functionality" but is project-internal. Inevitably, semantics drift between the two implementations.
**Prevention:** Golden-fixture tests that run the same saved query against Snowflake fixtures (mocked) and DuckDB (live) and assert identical row-for-row results. Flag semantic divergence loudly.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|---|---|---|
| Fusion subprocess integration | #3 (manifest drift), #7 (profiles.yml), #20 (binary name) | Version matrix CI, `doctor` command, env-var config |
| MetricFlow engine wiring | #1 (API stability), #9 (SqlClient stub), #16 (filter round-trip) | Pin version, adapter module, fixture suite |
| Ref/source rewrite (sentinels) | #2 (edge cases), #10 (time spines), #11 (xref/versioned), #12 (quoting) | Pre/post-flight scans, hostile fixtures, all four sentinels in phase 1 |
| Snowflake target | #4 (DDL maturity), #14 (packages.yml), #17 (feature gaps) | Capability matrix, `doctor` validation, live-warehouse CI |
| Databricks target | #4 (not GA) | Stub-with-warning default; never silent-fallback |
| DuckDB target | #29 (internal extension drift) | Cross-warehouse parity tests |
| Pre-commit hook | #5 (UX), #27 (git churn), #28 (partial writes) | Perf budget, deterministic output, staging+atomic-move |
| CLI / packaging | #6 (ELv2), #21 (Python version), #22 (Windows), #25 (log leaks) | License scan, CI matrix, `pathlib`, stderr filter |
| Legacy fast-path | #8 (divergence), #18 (autofix conflicts) | Both-paths CI diff, auto-detect spec version |
| Schema-file generation (`_semantic_views.yml`) | #13 (hand-edit conflicts), #28 (file sync) | Banner + hash, staging dir + atomic move |

---

## What to Verify Before Implementation

These items from the design notes are flagged as "Open questions & risks" and should be investigated in Phase 0 (or the earliest phase that touches them) with live fixtures before committing to the design:

1. **`dbtf parse` with a stub `profiles.yml`** — does Fusion actually tolerate a DuckDB-in-memory profile?
2. **`export.config.export_as` serialisation** — exact string values emitted by current Fusion version.
3. **Saved-query parameter coverage** — `where`, custom granularity, time spines all round-trip.
4. **Sentinel collision** — pre-flight scan verified against real-world user projects (anonymised).
5. **Fusion + metricflow version matrix** — minimum viable supported set.
6. **Databricks metric view package timeline** — check upstream repo status; confirm API shape before committing to the stub-with-warning pattern.
7. **Snowflake `dbt_semantic_view` package version to pin** — latest stable, changelog reviewed.

---

## Sources

- `_notes/semantic-manifest-loading.md` — primary source for API surfaces, sentinel design, open risks (HIGH confidence: grounded in `_metricflow` / `_fusion` source-line references).
- `_notes/dbt-semantic-views-codegen.md` — primary source for codegen architecture, open questions (HIGH confidence).
- `.planning/PROJECT.md` — constraints, decisions, compatibility matrix (HIGH confidence).
- `.planning/todos/pending/*.md` — feature scope todos (HIGH confidence).
- General codegen / pre-commit / multi-dialect SQL tool pitfalls (MEDIUM confidence: domain experience, not freshly verified against current best-practice docs). Web search unavailable in this research session — recommend verification of Pitfall 4 (warehouse feature maturity), Pitfall 6 (current ELv2 text), and Pitfall 21 (metricflow Python version support) with fresh Context7 / official-docs queries before Phase 0.
