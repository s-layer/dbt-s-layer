---
phase: 1
slug: parsing-pipeline
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-10-19
---

# Phase 1 — Validation Strategy

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Quick run command** | `uv run pytest -q` |
| **Full gate** | `prek run --all-files && uv run pytest` |
| **Fixture regen check** | `python scripts/regen_fixtures.py ... && git diff --exit-code tests/fixtures/artifacts` (needs both engines; manual) |

## Per-Plan Verification Map

| Plan | Requirement | Test file | What would make it fail |
|------|-------------|-----------|-------------------------|
| 01 | PARSE-02 | `tests/test_fixtures.py` | A regen that drops a producer, keeps macros, or loses a semantic model; dbt Core fixing the versioned-model drop |
| 02 | PARSE-01, PARSE-02 | `tests/test_artifacts.py`, `tests/test_project.py` | Loading legacy manifests without metricflow's transformations; raw tracebacks instead of named errors; ignoring `target-path` |
| 03 | PARSE-03 | `tests/test_refs.py` | Parsing `relation_name` instead of parts; losing version/package; silently picking one of several upstream nodes |
| 04 | CLI-05, CLI-03 | `tests/test_staleness.py`, `tests/test_doctor.py`, `tests/test_cli_help.py` | Non-semantic YAML marking stale; mtime ties marking stale; the missing-model warning not firing on `core_new` |

Every test must fail when the behaviour it names is wrong: tests are committed before the implementation in each plan, and each failure mode gets its own test.
