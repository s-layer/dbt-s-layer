---
phase: 01-parsing-pipeline
plan: 04
subsystem: cli
tags: [staleness, doctor, ruamel-yaml]

requires:
  - phase: 01-parsing-pipeline
    plan: 02
    provides: "load_project, artifact errors"
provides:
  - "`staleness.py`: scan_semantic_yaml, check_artifacts (ArtifactCheck with project + warnings)"
  - "`dbt-s-layer doctor --project-dir DIR`"
affects: [phase-3 (generate/check must call check_artifacts)]

key-files:
  created:
    - "src/dbt_s_layer/staleness.py"
    - "tests/test_staleness.py"
  modified:
    - "src/dbt_s_layer/_doctor.py"
    - "src/dbt_s_layer/cli.py"
    - "src/dbt_s_layer/artifacts.py (missing_artifact_error helper)"
    - "tests/test_doctor.py, tests/test_cli_help.py"

requirements-completed: [CLI-05]

completed: 2026-10-19
---

# Phase 01 Plan 04: staleness and doctor --project-dir Summary

**Missing or stale artifacts fail with the file to blame; dropped semantic models are named.**

## Accomplishments

- `scan_semantic_yaml` walks `model-paths` for `.yml`/`.yaml` that declare `semantic_models`, `metrics`, `saved_queries`, or a `models:` entry with a `semantic_model` key. Sources, plain model docs, time-spine config and empty files never count. Unreadable YAML is skipped with a warning.
- `check_artifacts` raises `ArtifactNotFoundError`, or `StaleArtifactError` naming the newest newer file (equal mtimes are fresh), then loads the project and warns for every declared semantic model the artifact lacks, with a cause-specific hint: dbt Core's versioned-model drop, or Fusion ignoring legacy-spec `semantic_models:`.
- `doctor --project-dir` prints `OK  artifacts: fresh (<producer>)` plus `WARN` lines, or `ERROR artifacts: ...` with exit code at least 1. Without the option doctor is unchanged.
- Manual check against a real `dbt parse` of `new_spec` with dbt Core 1.12.6: fresh with the `customers` warning, then stale after touching `orders.yml`.
- Red first (1d2bc2d), then green (90cb6cb). Mutation checks: `>=` instead of `>` fails the equal-mtime test; dropping the Fusion hint or the `semantic_model` counting rule fails their tests.

## Deviations

- A `models:` entry with `semantic_model: {enabled: false}` still counts for staleness (disabling one changes the artifact) but is not a declared semantic model.
- `check_artifacts` returns `ArtifactCheck(project, warnings)` rather than a bare list, so callers get the loaded project without loading twice.
- The Fusion-on-legacy-spec warning goes beyond the plan, which only named the dbt Core drop.
- CLI-03's output-directory check remains for Phase 3, as planned.
