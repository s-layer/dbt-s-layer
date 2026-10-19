# 03-02 Summary: generate and check

- `profiles.adapter_for_target` reads a target's `type` from profiles.yml (`--profiles-dir`,
  `DBT_PROFILES_DIR`, working directory, project directory, `~/.dbt`). Unknown targets list the
  known ones, and a Jinja or missing `type` asks for `--adapter`.
- `render_output(..., output_dir=)` refuses a generated model whose name a root-package node
  already has, or whose case-folded (custom schema, alias) matches a node without a `database`
  (not a legacy snapshot's `target_schema`). Nodes in the output directory are earlier output.
  `ModelNode` gains `original_file_path` and `config`.
- `_commands.run_generate` / `run_check`, wired as `dbt-s-layer generate` and `check` with
  `--project-dir` (default cwd), `--output-dir` (default `models/semantic`, relative to the
  project), `--target`, `--adapter`, `--profiles-dir`. Both run `check_artifacts` first and print
  its warnings. Errors print `ERROR: ...` and exit 1. `check` lists changed/missing/stale files
  and exits 1. An output directory outside `model-paths` gets a warning.
- The exports.py comment that said its lowercased relation check matches dbt now says it is
  stricter (03-RESEARCH finding 2).
- Tests committed red first: test_profiles.py (11), clash tests in test_output.py (11),
  test_cli_generate.py (22). Mutations checked: case-sensitive relation keys (1 failure),
  not skipping earlier output (1 failure).
