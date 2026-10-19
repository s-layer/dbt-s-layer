---
phase: 01-parsing-pipeline
plan: 03
subsystem: parsing
tags: [refs, manifest]

requires:
  - phase: 01-parsing-pipeline
    plan: 02
    provides: "SemanticProject"
provides:
  - "`refs.py`: RefTarget and build_ref_map, the input to Phase 2's sentinel rewrite"
affects: [phase-2]

key-files:
  created:
    - "src/dbt_s_layer/refs.py"
    - "tests/test_refs.py"

requirements-completed: [PARSE-03]

completed: 2026-10-19
---

# Phase 01 Plan 03: ref map Summary

**Each semantic model maps to one dbt node, the user's own `ref()` text, and its relation parts.**

## Accomplishments

- `build_ref_map(project)` returns a read-only `MappingProxyType`, sorted by lowercased semantic-model name, of `RefTarget(semantic_model, node_id, package, model_name, version, ref_expression, database, schema_name, alias)`.
- Relation parts come from `semantic_manifest.json` `node_relation`; a test proves they rebuild `relation_name` for both producers (quoted on Core, unquoted on Fusion).
- Fusion's `customers` resolves to `model.fx.customers.v2`, `version == 2`, `ref_expression == "ref('customers')"`.
- Seven failure modes each have their own test and raise `RefResolutionError` naming the semantic model and the producer: no upstream model, only non-model upstreams, two models, unknown node id, absent from manifest.json, no `ref()` expression, same name in two packages.
- Red first (429a8b9), then green (e152eea).

## Deviations

- `RefTarget.version` is `int | float | str | None`, not `int | None`: dbt model versions may be strings.
