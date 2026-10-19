# 05-03 Summary: hook definition, scale test, end to end

- `.pre-commit-hooks.yaml`: `id: dbt-s-layer`, `entry: dbt-s-layer pre-commit`,
  `language: python`, `files: (\.ya?ml|(^|/)(sem|exp)__[^/]*\.sql)$`, `require_serial: true`,
  `minimum_pre_commit_version: "3.0.0"`. Tests check the fields, the console script, and the
  `files` pattern on 11 paths (red first).
- The hook starts the first project's `dbt parse` before importing the generator, so the
  0.7 s Fusion parse overlaps the 0.8 s import.
- tests/test_hook_scale.py: the hook as a fresh process on the 100/50 project, with the
  engines from `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE` (skipped without them): creates 150
  models (exit 1), an edited YAML file changes them (exit 1, under 5 s with Fusion), a rerun is
  clean (exit 0).
- Found by the end-to-end run, fixed test-first: a local package's own `dbt_project.yml`
  (snowflake_spec's `packages/fx_dep`) made the hook parse the package alone, which fails ("No
  profile specified"). Discovery now takes the nearest `dbt_project.yml` that names a
  `profile:`, falling back to the nearest one.
- README "Pre-commit hook": the `.pre-commit-config.yaml` snippet, what the hook does, its
  options, and the timings for both engines.
