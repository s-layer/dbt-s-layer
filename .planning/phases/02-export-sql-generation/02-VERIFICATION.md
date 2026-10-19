---
phase: 02-export-sql-generation
verified: 2026-10-19
status: passed
---

# Phase 2 — Verification

Verified on 2026-10-19 against the ROADMAP success criteria. Gates: `prek run --all-files` and `uv run pytest` on Python 3.11.

## Success criteria

| # | Criterion | Evidence | Result |
|---|-----------|----------|--------|
| 1 | Each export yields rendered SQL with the expected aggregations and group-bys | `test_dbt_renders_each_model_to_metricflows_own_sql`: every body, rendered by jinja2 with refs resolved through manifest.json, equals metricflow's SQL against the physical tables, for both engines; `test_revenue_by_day_body` pins one literal | ✅ |
| 2 | Sentinel forms round-trip across a hostile fixture (3-part Snowflake identifiers, versioned refs, cross-project refs) | `fusion_export` has 3-part `ANALYTICS.MARTS.*` relations; `test_tokens_resolve_to_refs_built_from_the_node` and `test_models_use_every_ref_form` cover `__REF__`, `__XREF__` (`ref('fx_dep', 'stores')`), `__REFV__` (`v=1` pinned, `v=2` latest). `__SOURCE__` cannot occur (dbt rejects `source()` in a semantic model); it is reserved and checked by pre/post-flight | ✅ (3 producible forms) |
| 3 | Pre-flight aborts when project names collide with a reserved prefix | `test_preflight_*`: expr, saved query filter, referenced model alias, each prefix, any case | ✅ |
| 4 | Post-flight refuses unresolved sentinels | `test_postflight_refuses_leftover_prefixes`, `test_rewrite_refuses_unknown_tokens`, unmapped time spine | ✅ |
| 5 | `config(materialized=...)` follows the export's YAML | `test_one_model_per_export_with_resolved_config`, `test_config_blocks`; same result for both engines | ✅ |

## Notes

- Manual end-to-end: generated models built in DuckDB with dbt Core 1.12.6 (02-03-SUMMARY.md).
- Fixture regeneration ran twice with no diff.
- Test-first evidence is in the commit history.
