---
phase: 01-parsing-pipeline
plan: 02
subsystem: parsing
tags: [metricflow, pydantic, artifacts]

requires:
  - phase: 01-parsing-pipeline
    plan: 01
    provides: "Fixture artifact sets"
provides:
  - "`errors.py`: DbtSLayerError and ProjectNotFound/ArtifactNotFound/ArtifactInvalid/StaleArtifact/RefResolution errors"
  - "`artifacts.py`: resolve_target_dir, read_semantic_manifest, read_dbt_manifest, DbtManifest models"
  - "`project.py`: SemanticProject, load_project, producer_label"
affects: [01-03, 01-04, phase-2, phase-3]

key-files:
  created:
    - "src/dbt_s_layer/errors.py"
    - "src/dbt_s_layer/_yaml.py"
    - "src/dbt_s_layer/artifacts.py"
    - "src/dbt_s_layer/project.py"
    - "tests/test_artifacts.py"
    - "tests/test_project.py"
  modified:
    - "pyproject.toml / uv.lock (ruamel.yaml >= 0.18)"
    - "tests/conftest.py (make_project fixture, DBT_TARGET_PATH isolation)"

requirements-completed: [PARSE-01, PARSE-02]

completed: 2026-10-19
---

# Phase 01 Plan 02: artifact readers and SemanticProject Summary

**`load_project(dir)` gives the same typed result for all three producers, with no per-spec flag.**

## Accomplishments

- `read_semantic_manifest` parses with metricflow's pydantic v1 model, applies `transform_dbt_generated_manifest` (the same transformations `parse_manifest_from_dbt_generated_manifest` runs), then metricflow's `SemanticManifestValidator`, then builds a `SemanticManifestLookup`. Fusion skips semantic validation without `dbt_cloud.yml`, so running the validator here is what catches a broken manifest with a readable message.
- Every failure is a named error whose message names the file: missing (says to run `dbt parse` with dbt Core or Fusion, in the project dir), unreadable, invalid JSON, wrong shape (pydantic field paths, no traceback), and metricflow rejections (the validator's own message).
- `read_dbt_manifest` keeps only model nodes and semantic models, so a full untrimmed manifest loads.
- The target dir follows dbt: `DBT_TARGET_PATH`, then `target-path`, then `target`; a Jinja `target-path` is refused with a pointer to `DBT_TARGET_PATH`.
- `load_project` rejects a manifest.json whose `project_name` differs from dbt_project.yml.
- Red first (6dabaae), then green (33ae1fe). Mutation check: skipping the dbt transformations fails 5 tests; ignoring `DBT_TARGET_PATH` fails its test.

## Deviations

- `SemanticProject` is a frozen dataclass, not a pydantic v2 model: it holds metricflow's pydantic v1 manifest, and embedding a v1 model in a v2 one triggers pydantic's mixing warning (CONTEXT: no v1/v2 mixing in one model).
- The plan's lowercasing test was retargeted to semantic-model names: metricflow's `LowerCaseNamesRule` lowercases those, not metric names.
- Added `ProjectNotFoundError` for a missing or unreadable dbt_project.yml.
