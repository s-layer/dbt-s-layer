---
phase: 01-parsing-pipeline
verified: 2026-10-19
status: passed
---

# Phase 1 — Verification

Verified on 2026-10-19 against the ROADMAP success criteria. Gates: `prek run --all-files` and `uv run pytest` (175 passed) on Python 3.11.

## Success criteria

| # | Criterion | Evidence | Result |
|---|-----------|----------|--------|
| 1 | Legacy-spec project (dbt Core) loads into a `SemanticProject` with all semantic models, metrics and saved queries | `test_load_project_contents[core_legacy]`: `orders`, `order_items`; `revenue`, `items_sold`; `revenue_by_day` with export `exp_revenue_by_day`; a `SemanticManifestLookup` builds | ✅ |
| 2 | New-spec project parsed by Fusion or dbt Core 1.12+ loads the same way, no code-path differences | Same test for `fusion_new` and `core_new`; nothing in `artifacts.py`/`project.py` branches on engine or spec. `test_legacy_and_new_spec_core_sets_agree` compares metric names and export configs | ✅ |
| 3 | Every semantic model's relation is mapped via `manifest.json` `depends_on.nodes` | `build_ref_map` maps every semantic model in all three sets to one node with its `ref()` expression and relation parts (`test_refs.py`, 23 tests). The sentinel tokens themselves are Phase 2 (EXPRT), which consumes this map, as 01-CONTEXT.md scoped it | ✅ (map; sentinels in Phase 2) |
| 4 | A stale or missing `semantic_manifest.json` exits with a clear error telling the user to run `dbt parse` | `check_artifacts` and `doctor --project-dir` (`test_staleness.py`, `test_doctor.py`); manual run against a real dbt Core parse showed the stale message naming `models/marts/orders.yml`. `doctor` is the only command that reads a project today; Phase 3's `generate`/`check` must call `check_artifacts` | ✅ (existing commands) |

## Requirements

- PARSE-01: the tool only reads artifacts; nothing invokes a parser outside `scripts/regen_fixtures.py` (a dev tool).
- PARSE-02: one loader for both engines and both specs.
- PARSE-03: `refs.build_ref_map`.
- CLI-05: `staleness.check_artifacts`, surfaced by `doctor --project-dir`; Phase 3 commands reuse it.
- CLI-03: Phase 1 part done (artifact presence and freshness); output-directory writability remains for Phase 3.

## Notes

- Fixture regeneration (`scripts/regen_fixtures.py --fusion ... --core ...`) was run twice with Fusion 2.0.8 and dbt Core 1.12.6; the second run produced no diff.
- Test-first evidence is in the commit history: each plan's tests were committed failing before its implementation.
