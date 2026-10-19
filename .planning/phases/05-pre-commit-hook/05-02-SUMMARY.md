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

## Review round 1 (PR #8)

- Engine by YAML spec: with no `--dbt`, a project that declares a legacy-spec semantic
  model (top-level `semantic_models:`) itself, in a `local:` package from packages.yml /
  dependencies.yml, or in a package under `packages-install-path` is parsed with dbt Core,
  found with `_doctor.detect_engines()` (`--version`). No dbt Core >= 1.7 is an error naming
  the file, the engines found, and the ways out (install Core, `--dbt`, new spec,
  `--no-parse`). Other projects keep `dbtf`, then `dbt`, without a `--version` probe. Before
  this, with both on PATH the hook always used Fusion, which drops legacy models.
- `DeclaredSemanticModel.legacy` detection reused: the YAML scan and `read_dbt_project` moved
  to `_project_yaml` (no metricflow import), so the hook can choose before the first parse
  starts; `profiles` now imports from there too and is light.
- In the hook (`Options.missing_is_error`), a declared semantic model missing from
  semantic_manifest.json is an `ArtifactIncompleteError` before anything is written, so a
  wrong parse removes no generated file. `generate`, `check` and `doctor` still warn.
- Project selection: starting at the closest dbt_project.yml, a project further up that lists
  it as a `local:` package owns the file, whether or not the package names a `profile:`; a
  package nothing lists and that names no profile goes to the nearest project above with one.
  Deviation from the review's "else the outermost project with a profile": that would send
  `integration_tests/` (which installs its parent with `local: ../`) to the parent package,
  the usual dbt package layout; a guard test pins it.
- `time_spine:` added to the trigger keys.
- profiles.yml found once per project (`--profiles-dir`, else `profiles.find_profiles` order)
  and passed to `dbt parse` as an absolute `--profiles-dir` and to generate.
- Tests committed red first (tests/test_hook.py, +12 failing, +3 guards): the fake engine now
  answers `--version` (`dbtf` Fusion, otherwise dbt Core, or as given) and the fixture
  points HOME away from any real `~/.dbt`. `test_dbtf_is_preferred` now uses the new-spec
  fusion_export project (core_export is legacy spec). One assertion was relaxed after the red
  commit: the legacy error names the first legacy file in path order (customers.yml), not
  orders.yml.
