# Phase 5: Pre-Commit Hook - Context

**Gathered:** 2026-10-19
**Status:** Ready for planning
**Source:** ROADMAP Phase 5, REQUIREMENTS HOOK-01/02, CLAUDE.md "Pre-commit Hook Contract",
05-RESEARCH.md ("Recommendations for planning")

<domain>
## Phase Boundary

Ship one pre-commit hook that keeps a project's generated files in sync with its semantic YAML:
when a staged file holds `semantic_models:`, `metrics:` or `saved_queries:`, the hook parses the
project and regenerates, and fails the commit when it changed anything so the user stages the
result. Make `generate` fast enough for the 5-second budget on 100 semantic models and 50
exports.

**In scope:** export rendering speed (shared engine set-up, parallel explain), the
`dbt-s-layer pre-commit` command, `.pre-commit-hooks.yaml`, README, an end-to-end run with
pre-commit and prek, the scale fixture generator and its timings.

**Out of scope:** making dbt Core's own parse faster (5.4 s start-up-bound, 05-RESEARCH
finding 1); caching rendered SQL between runs; Windows parallelism (no `fork`); Databricks and
DuckDB adapters (Phases 6 and 7). The release pipeline that publishes a tagged hook repo is
Phase 8.
</domain>

<decisions>
## Implementation Decisions

- **Option (a): the hook parses, then generates** (05-RESEARCH "Options"). The only option that
  regenerates (criterion 1). A check-only hook is not faster and asks the user to run two
  commands.
- **New command `dbt-s-layer pre-commit [FILES]...`**, the hook's entry point. Options:
  `--project-dir`, `--output-dir`, `--target`, `--adapter`, `--profiles-dir` (as `generate`),
  `--dbt` (executable that parses; default `dbtf` when on PATH, else `dbt`) and `--no-parse`.
  `generate` and `check` are unchanged.
- **Relevance, decided by the hook from content.** A passed file triggers when it is a
  `dbt_project.yml`, a `.yml`/`.yaml` whose text has a line starting (after indentation and an
  optional `- `) with `semantic_models:`, `semantic_model:`, `metrics:` or `saved_queries:`, or a
  file inside the project's output directory. A regex, no YAML parse, no metricflow import. No
  passed files at all (`pass_filenames: false` or a manual run) means: run for `--project-dir` or
  the current directory.
- **Project discovery.** `--project-dir` pins one project and ignores files outside it.
  Otherwise each relevant file maps to the nearest ancestor directory with a `dbt_project.yml`
  (stopping at the current directory's root); a file under no project is ignored. Projects run in
  sorted order.
- **Parse.** `<dbt> parse` run in the project directory, plus `--target T` / `--profiles-dir D`
  when given; dbt's output is shown only when it fails (exit 1, last 30 lines, and how to skip
  with `--no-parse`). No version probe (dbt Core's `--version` costs 3.2 s). Engine not found:
  an error naming `--dbt` and `--no-parse`.
- **Exit codes.** 0 when nothing relevant was passed or every project was already up to date;
  1 when any file was written, changed or removed (message lists them and says to stage them and
  commit again) or on any error. pre-commit does not see new untracked files (05-RESEARCH
  finding 5), so the hook's own exit code carries "files were modified".
- **Rendering speed.** `render_exports` builds one `MetricFlowEngine` set-up and gives each
  saved query a fresh planner (new `DataflowPlanBuilderCache`, `DataflowPlanBuilder` with a copy
  of the source-node visitor, `DataflowToSqlPlanConverter`). With at least 8 saved queries, more
  than one CPU and the `fork` start method, saved queries are explained in a forked process
  pool (workers = min(CPUs, 8, saved queries)); errors return as messages and the first in
  sorted order is raised, as sequentially. `render_exports(..., jobs=)` and the environment
  variable `DBT_S_LAYER_JOBS` (a positive integer) override the worker count. Output is
  byte-identical to Phase 2's engine-per-query rendering.
- **Hook definition.** `.pre-commit-hooks.yaml`: `id: dbt-s-layer`, `name: dbt-s-layer
  generate`, `entry: dbt-s-layer pre-commit`, `language: python`,
  `files: (\.ya?ml|/(sem|exp)__[^/]*\.sql)$`, `require_serial: true` (without it pre-commit runs
  file partitions in parallel, 05-RESEARCH finding 5), `pass_filenames: true`.
- **Scale fixture.** `scripts/make_scale_project.py` writes the 100/50 project; not committed as
  a fixture (it needs a parse). An opt-in test (engines from `DBT_S_LAYER_FUSION` /
  `DBT_S_LAYER_CORE`, as for the compile test) runs the hook on it.

### Claude's Discretion

- Message wording; the parallelism threshold (8) and cap (8 workers).
- `pre-commit` prints `generate`'s per-project summary line.
</decisions>

<canonical_refs>
## Canonical References

- `.planning/phases/05-pre-commit-hook/05-RESEARCH.md`
- `.planning/phases/02-export-sql-generation/02-RESEARCH.md` finding 1 (engine per saved query)
- `src/dbt_s_layer/exports.py`, `_commands.py`, `cli.py`, `staleness.py`
</canonical_refs>

<deferred>
## Deferred Ideas

- Triggering on deletion of a semantic YAML file, or on removal of its last semantic key
  (pre-commit passes only existing files); `dbt-s-layer check` in CI covers it.
- Publishing the hook repo under a release tag: Phase 8.
</deferred>

---

*Phase: 05-pre-commit-hook*
*Context gathered: 2026-10-19*
