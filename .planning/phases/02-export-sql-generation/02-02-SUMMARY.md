---
phase: 02-export-sql-generation
plan: 02
subsystem: exports
tags: [sentinels, refs]

requires:
  - phase: 01-parsing-pipeline
    plan: 03
    provides: "build_ref_map"
provides:
  - "`sentinels.py`: build_sentinel_table, preflight, rewrite, postflight, render_ref, escape_jinja"
affects: [phase-2, phase-3]

key-files:
  created:
    - "src/dbt_s_layer/sentinels.py"
    - "tests/test_sentinels.py"
  modified:
    - "src/dbt_s_layer/artifacts.py"
    - "src/dbt_s_layer/errors.py"

requirements-completed: [EXPRT-02, EXPRT-04, EXPRT-05]

completed: 2026-10-19
---

# Phase 02 Plan 02: sentinel table Summary

**Relations become two-part tokens in a copy of the manifest; tokens become refs built from the node.**

## Accomplishments

- One token per node (`__REF__`, `__XREF__`, `__REFV__` + `.n<i>_<name>`), shared by semantic models on the same node; time spines (new and legacy config) mapped by `relation_name`; an unmapped time spine fails only the exports that use it.
- Pre-flight walks every semantic manifest string (paths like `semantic_models[orders].dimensions[status].expr`) and each referenced node's name and alias, case-insensitively.
- Rewrite escapes `{{`, `{%`, `{#` and a trailing `{` in metricflow's text; tests render the result with jinja2 and get the original text back.
- Post-flight refuses any reserved prefix left, in any case.
- Mutation checks: dropping the trailing-brace escape, a shallow manifest copy, case-sensitive matching, never naming the package, no `__REFV__`, no time spine mapping, no legacy spine rewrite and no node pre-flight each fail at least one test.
