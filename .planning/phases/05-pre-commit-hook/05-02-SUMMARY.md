# 05-02 Summary: the `pre-commit` command

- New `src/dbt_s_layer/_hook.py` and `dbt-s-layer pre-commit [FILES]...` with `generate`'s
  options plus `--dbt` and `--parse/--no-parse`. The module imports neither metricflow nor the
  generator until a file is relevant: a commit with only unrelated files exits 0 in about
  0.12 s (a test blocks the metricflow modules and runs it).
- Relevance: `dbt_project.yml` of the project, any file inside the project's output directory,
  or a `.yml`/`.yaml` with a line whose key (after indentation and an optional `- `) is
  `semantic_models`, `semantic_model`, `metrics` or `saved_queries` (a bytes regex, no YAML
  parse).
- Projects: `--project-dir` pins one and ignores files outside it; otherwise the nearest
  `dbt_project.yml` above each file, not above the working directory (the repo root under
  pre-commit). No files: `--project-dir` or the working directory.
- Each project: `<dbt> parse` (with `--target` and an absolute `--profiles-dir` when given)
  in the project directory, then `_commands.generate_project` (the body of `run_generate`,
  now returning the output directory and the diff). dbt is `--dbt` (a path relative to the
  working directory, or a name on PATH), else `dbtf`, else `dbt`; none found is an error that
  names `--dbt` and `--no-parse`. A failed parse prints its exit code, the last 30 lines of its
  output, and the `--no-parse` hint; that project is not generated.
- Exit 1 when any file was written, changed or removed (listed as `new:`, `changed:`,
  `removed:` relative to the working directory, then "Stage them (git add) and commit
  again."), or on any error; otherwise 0.
- One test was corrected after its red commit: the edit simulation dated the YAML 10 s into
  the future, which the fake parse could not overtake; it now ages the artifacts instead.
- Tests committed red first: tests/test_hook.py (35 cases with a fake `dbt` script that logs
  its argv and directory and copies fixture artifacts into `target/`).
