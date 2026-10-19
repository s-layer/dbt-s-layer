# Phase 3 Verification

**Status:** passed
**Date:** 2026-10-19

## End to end

On fresh copies of the fixture projects, with dbt Core 1.12.6 (dbt-duckdb 1.11.0) and dbt Fusion 2.0.8:

| Step | export_legacy (Core, DuckDB) | export_spec (Fusion, Snowflake) |
|---|---|---|
| `dbt parse` | exit 0 | exit 0 |
| `dbt-s-layer generate --project-dir P` | 6 files written (5 exports + `_semantic_views.yml`) | same |
| `generate` again | "up to date", nothing rewritten | - |
| `dbt parse` with the generated models | exit 0 | exit 0 |
| `dbt-s-layer check` after the re-parse | exit 0, "up to date" | exit 0 |
| `dbt build` | PASS=11 ERROR=0 | (no warehouse) |
| `dbt-s-layer doctor --project-dir P` | - | artifacts fresh, output directory writable |

The re-parse puts the generated models into manifest.json, and `check` still passes, so earlier
output is not mistaken for a clash with the user's own models.

## Success criteria

1. `generate --project-dir . --output-dir models/semantic/` writes `exp__*.sql` and
   `_semantic_views.yml`. No `sem__*.sql` yet: those come with Phase 4. The writer already owns
   the `sem__` prefix. (test_cli_generate.py)
2. Byte-identical reruns: test_rendering_is_byte_stable, test_second_apply_rewrites_nothing,
   test_generate_twice_changes_nothing, and the end-to-end rerun.
3. Atomic: test_failure_while_staging_changes_nothing, test_files_reach_place_by_os_replace,
   test_hand_written_owned_file_is_refused (nothing written).
4. Header on every file: test_sql_files_start_with_the_header, test_schema_file.
5. `check` exits non-zero on drift: test_check_* in test_cli_generate.py.
6. `--project-dir`, `--output-dir`, `--target`, `--adapter` (plus `--profiles-dir`):
   test_cli_generate.py dialect tests and test_profiles.py.

Requirements: OUTPT-01..05, CLI-01, CLI-02, CLI-04 complete. CLI-03 complete
(output directory check added).

## Gates

- `uv run pytest`: 380 passed
- `prek run --all-files`: clean
