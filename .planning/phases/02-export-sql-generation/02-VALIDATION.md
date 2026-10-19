---
phase: 2
slug: export-sql-generation
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-10-19
---

# Phase 2 — Validation Strategy

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Quick run command** | `uv run pytest -q` |
| **Full gate** | `prek run --all-files && uv run pytest` |
| **Fixture regen check** | `python scripts/regen_fixtures.py ... && git diff --exit-code tests/fixtures/artifacts` (needs both engines; manual) |

## Per-Plan Verification Map

| Plan | Requirement | Test file | What would make it fail |
|------|-------------|-----------|-------------------------|
| 01 | EXPRT-01, 02 | `tests/test_fixtures.py`, `tests/test_refs.py` | A regen without `dbt deps`; losing the package or versioned semantic model; Fusion fixing export_as inheritance |
| 02 | EXPRT-02, 04, 05 | `tests/test_sentinels.py` | Mutating the caller's manifest; refs copied from the user's expression; unescaped Jinja; case-sensitive collision checks |
| 03 | EXPRT-01, 03 | `tests/test_exports.py` | A shared engine; materialisation from the rendered config (Fusion); always writing `schema`; a body dbt would render differently from metricflow's SQL |
