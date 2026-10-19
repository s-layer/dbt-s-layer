# Phase 5 Verification

**Status:** passed (HOOK-02's 5 s holds with dbt Fusion; with dbt Core the hook takes 10 to 12 s,
because dbt Core's own `dbt parse` takes 5.4 s or more on the same project)
**Date:** 2026-10-19

## End to end with pre-commit 4.7.0 (Fusion 2.0.8)

A git repository with the `snowflake_spec` fixture project in `analytics/` (so the project is
in a subdirectory and has a local package with its own `dbt_project.yml`), `dbt deps` run,
dbt Fusion as `dbt` on PATH (no `dbtf`), and:

```yaml
repos:
  - repo: /home/user/dbt-s-layer
    rev: <HEAD of this branch>
    hooks:
      - id: dbt-s-layer
        args: [--profiles-dir, analytics/profiles, --target, snowflake]
```

`pre-commit install`, then:

| Step | Result |
|---|---|
| `git add -A; git commit` (first time; pre-commit builds the hook environment) | exit 1 in 10.8 s; "Failed", lists 10 `new:` files under `analytics/models/semantic/`, "Stage them (git add) and commit again." |
| `git add -A; git commit` | exit 0 in 1.6 s, "Passed", commit made with the 10 files |
| Edit `orders.yml` (`expr: amount` to `expr: amount * 1.0`), `git add`, `git commit` | exit 1 in 1.5 s; "files were modified by this hook"; 5 `changed:` files (4 exports, `sem__orders.sql`) |
| `git add -A; git commit` | exit 0 in 1.7 s, "Passed"; commit holds the YAML and the 5 files |
| `pre-commit run --all-files` | exit 0, "Passed", working tree clean |
| Commit touching only `README.md` | "(no files to check) Skipped", 0.36 s |

The first attempt of this run failed: the hook attributed `analytics/packages/fx_dep/models/
stores.yml` to the package's own `dbt_project.yml` and ran `dbt parse` there ("No profile
specified in dbt_project.yml"). Fixed test-first (05-03-SUMMARY.md) and rerun as above.

## End to end with prek 0.5.6

- Fusion, a clone of the repository above: `prek install`; revert the YAML edit and commit:
  exit 1 in 4.4 s (including the hook environment install), "files were modified by this hook",
  4 `changed:`; stage and commit: exit 0 in 1.3 s; `prek run --all-files`: exit 0 in 1.2 s,
  tree clean. A hand edit to `sem__orders.sql` committed: exit 1, `changed:
  analytics/models/semantic/sem__orders.sql`, and the file is put back.
- dbt Core 1.12.6 (only `dbt` Core on PATH), the `snowflake_legacy` project at the repository
  root: first commit exit 1 in 6.8 s with 10 `new:` files; second exit 0 (5.9 s); an edit to
  `agg:` in `orders.yml`: exit 1 (5.2 s), 5 `changed:`; second commit exit 0;
  `prek run --all-files` exit 0, tree clean.

## Timings on 100 semantic models and 50 exports (HOOK-02)

`scripts/make_scale_project.py` (99 fact models joined to `customers`, one YAML file each,
50 saved queries of two metrics each, Snowflake target); 4-core Linux VM. The hook run is
`dbt-s-layer pre-commit --dbt <engine> --profiles-dir profiles models/marts/fact_0NN.yml` in
the project after editing that file, as pre-commit runs it (a fresh process).

| | dbt Fusion 2.0.8 | dbt Core 1.12.6 |
|---|---|---|
| `dbt parse` after one edit | 0.61 to 0.73 s | 5.45 to 6.64 s |
| `generate` before this phase | 34.0 to 35.1 s | 32.9 to 34.2 s |
| `generate` after this phase | 3.4 to 4.6 s | 3.7 to 4.8 s |
| **Hook after an edit (5 runs)** | **4.03 to 4.32 s** | 10.4 to 12.5 s |
| Hook rerun, nothing to change | 4.4 s | 10.1 s |
| Hook with no relevant file | 0.12 s | 0.13 s |

`DBT_S_LAYER_JOBS=1` (one process): `generate` 7.2 s. tests/test_hook_scale.py automates
the Fusion budget check (2 passed with both engines set).

## Success criteria

1. A project that adds the hook regenerates on YAML with `semantic_models:`, `metrics:` or
   `saved_queries:` keys: end to end above (pre-commit and prek, both engines);
   tests/test_hook.py (relevance regex, project discovery, parse and generate, exit codes).
2. Under 5 s on 100 semantic models and 50 exports: 4.03 to 4.32 s with Fusion. Not with dbt
   Core (10 to 12 s): its parse alone exceeds the budget (05-RESEARCH finding 1); documented in
   the README.
3. Deterministic, a second run changes nothing: the second commit of every cycle above passes,
   `pre-commit run --all-files` / `prek run --all-files` leave the tree clean,
   `test_first_run_writes_and_fails_second_run_passes`, the scale test's rerun. Export output
   is byte-identical to Phase 2's (test_exports.py reference tests).

Requirements: HOOK-01, HOOK-02 complete.

## Not verified

- Timings on other machines: 4 cores here, and the machine's load varied (the same `generate`
  measured 3.4 s and 4.9 s minutes apart). On 2 cores the Fusion hook should land near 5 s.
- Windows: no `fork`, so exports render in one process (about 7 s for `generate` on the 100/50
  project); not run.
- The hook from the GitHub URL with a tag: no release tag exists yet (Phase 8). The end-to-end
  runs used the local repository path at this branch's commit.

## Gates

- `uv run pytest`: 754 passed, 4 skipped (the compile and scale tests, without engines)
- With `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE` set: 758 passed
- `prek run --all-files`: clean
