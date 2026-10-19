# Phase 1: Parsing Pipeline - Context

**Gathered:** 2026-10-19
**Status:** Ready for planning
**Source:** Phase 0 close-out decisions + hands-on research (01-RESEARCH.md)

<domain>
## Phase Boundary

Load the dbt parse artifacts (`target/semantic_manifest.json` + `target/manifest.json`) from a dbt project directory into typed internal models, resolve every semantic model to the dbt node it reads from, and fail with an actionable message when the artifacts are missing or stale.

**In scope:**
- Committed fixture artifacts from three producers: Fusion 2.0.8 (new spec), dbt Core 1.12.6 (new spec), dbt Core 1.12.6 (legacy spec), plus a script that regenerates them
- `SemanticProject` loader (PARSE-01, PARSE-02)
- Ref map: semantic model -> dbt node + the user's own `ref()` expression + relation parts (PARSE-03)
- Missing/stale artifact errors (CLI-05) and the matching `doctor` check (CLI-03 Phase 1 part)
- Warning when a semantic model declared in YAML is absent from the artifact (the dbt Core versioned-model drop)

**Out of scope:**
- Sentinel rewriting, `explain()`, collision pre-flight, unresolved-token post-flight (Phase 2)
- `generate` / `check` commands and file output (Phase 3)
- Cross-project (`__XREF__`) fixtures: needs a dbt Mesh/loom setup, Phase 2

</domain>

<decisions>
## Implementation Decisions

### Engines
- Either engine is supported (Anentropic, 2026-10-19): Fusion 2.x or dbt Core >= 1.7. The tool never invokes the parser; it reads artifacts (PARSE-01).
- Error text names both commands: "run `dbt parse` (dbt Core or Fusion) in <project-dir>".

### Loading
- Semantic manifest: `metricflow_semantics.model.dbt_manifest_parser.parse_manifest_from_dbt_generated_manifest(text)`. Never `PydanticSemanticManifest.parse_obj` directly (legacy manifests break `SemanticManifestLookup`).
- Manifest types stay as metricflow's pydantic-v1 objects; our own models are pydantic v2 (`frozen=True`). No v1/v2 mixing in one model.
- `manifest.json`: read with `json` and validate only the keys we use (`metadata.dbt_version`, `metadata.project_name`, `nodes[*]` for models, `semantic_models[*]`) into small pydantic v2 models with `extra="ignore"`.
- Target dir: `<project-dir>/<target-path>`, where `target-path` comes from `dbt_project.yml` (default `target`), overridable by `DBT_TARGET_PATH`, matching dbt.

### Ref map
- Key: semantic model name (lowercased, as metricflow's `LowerCaseNamesRule` does).
- Value: `RefTarget(node_id, package, model_name, version, ref_expression, database, schema, alias)`. `ref_expression` is `manifest.json` `semantic_models[...].model` verbatim.
- Relation parts come from `node_relation.database/schema_name/alias`. `relation_name` is never parsed (Fusion leaves it unquoted, Core quotes it).
- A semantic model whose `depends_on.nodes` is not exactly one `model.*` node is an error naming the semantic model.

### Staleness (CLI-05)
- Stale = `semantic_manifest.json` mtime older than the newest `.yml`/`.yaml` under the project's `model-paths` that declares `semantic_models:`, `metrics:`, `saved_queries:`, or a `models:` entry with `semantic_model:`.
- YAML is read with `ruamel.yaml` (safe loader). Added as a runtime dependency now; Phase 3 needs it for writing anyway.

### Claude's Discretion
- Module layout under `src/dbt_s_layer/` (suggested: `artifacts.py`, `project.py`, `refs.py`, `staleness.py`, `errors.py`).
- Exception hierarchy shape, as long as the CLI maps each to a one-line message and exit code 1.

</decisions>

<deferred>
## Deferred Ideas

- Reporting the dbt Core 1.12.6 versioned-model drop upstream (Anentropic to decide).
- `osi_document.json` as an input for semantic-view adapters (Phase 4 research).
- A minimum supported Fusion version (only 2.0.8 tested).

</deferred>
