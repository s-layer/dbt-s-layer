# Phase 5: Pre-Commit Hook - Research

**Researched:** 2026-10-19, hands-on, dbt Core 1.12.6 (dbt-snowflake 1.12.3, dbt-duckdb 1.11.0),
dbt Fusion 2.0.8, pre-commit 4.7.0 and prek 0.5.6, on a 4-core Linux VM. The scale fixture is
written by `scripts/make_scale_project.py`: 100 semantic models (one `customers` model and 99
fact models joined to it, one YAML file each, new spec) and 50 saved queries with one export
each (two metrics, grouped by `metric_time` day and `customer__country`), Snowflake target.

## Summary

- **The design tension.** `generate` refuses artifacts older than the semantic YAML (CLI-05).
  In a pre-commit hook the user has just edited the YAML, so the artifacts are stale by
  definition. A hook that regenerates (success criterion 1) has to parse first: option (a).
  Option (b), a hook that only checks, cannot meet criterion 1, and is not faster: `check`
  renders everything `generate` does.
- **The budget is spent by dbt-s-layer, not by dbt.** Before this phase `generate` took 34 s on
  the 100/50 project; Fusion's parse took 0.65 s. The cost is one `MetricFlowEngine` per saved
  query (Phase 2's determinism fix), each rebuilding every semantic model's data set (finding 2).
- **Fixable without changing a byte of output.** One engine, with fresh planner state per saved
  query, renders byte-identical SQL in 5.3 s; explaining the saved queries in forked workers
  brings `generate` to about 3 s (finding 3).
- **Reachable with Fusion, not with dbt Core.** Fusion parse (0.65 s) plus the faster
  `generate` (about 3 s) fits in 5 s. dbt Core's own `dbt parse` takes 5.4 s here even with a
  warm partial parse (3.2 s of it is start-up: `dbt --version` alone takes 3.2 s), so no hook
  design meets 5 s with dbt Core on this machine (finding 1).
- **pre-commit does not notice new files.** It detects modification by comparing `git diff`
  before and after the hook; a newly generated, untracked `exp__*.sql` is invisible to it. The
  hook must exit 1 itself when it changed anything (finding 5).
- **Recommendation:** option (a), as a new `dbt-s-layer pre-commit` command: filter the passed
  files by content (cheap, no metricflow import), find each file's dbt project, run `dbt parse`
  with whichever engine is on PATH (`dbtf`, then `dbt`; `--dbt` overrides, `--no-parse` skips),
  then `generate`, and exit 1 when files changed. Plus the two rendering speed-ups.

## Finding 1: dbt parse timings (verified)

`dbt parse --profiles-dir profiles` in the 100/50 project, wall clock, three runs each:

| Run | dbt Core 1.12.6 | Fusion 2.0.8 |
|---|---|---|
| Full parse (Core: `--no-partial-parse`; first Fusion run) | 9.3 - 9.8 s | 1.39 s |
| After editing one semantic YAML file (Core: partial parse) | 5.45 - 5.70 s | 0.61 - 0.69 s |
| Nothing changed | 5.38 - 5.44 s | - |
| `dbt --version` | 3.21 s | 0.02 s |

The `duckdb` target makes no difference on Core (5.66 s). Core's partial parse is already
doing almost nothing: start-up dominates. Both engines rewrite `semantic_manifest.json` on every
parse, unchanged or not, so after a hook's parse the staleness check (CLI-05) passes.

So HOOK-02's 5 seconds hold with Fusion; with dbt Core the parse alone is over budget. dbt-s-layer
cannot change that (it does not run dbt in-process, and Core's start-up is importing dbt and the
adapter). Documented as a known limit of the engine, not a design choice.

## Finding 2: where `generate` spends its time (verified)

`dbt-s-layer generate` on the parsed 100/50 project: 34.2, 34.0, 32.9 s (Core artifacts);
34.1, 34.0, 35.1 s (Fusion artifacts). `check` 36.0 s. 151 files: 100 `sem__`, 50 `exp__`, the
schema file. cProfile (inflated about 2.7x):

| Step | Profiled | Share |
|---|---|---|
| 50 x `MetricFlowEngine.__init__` | 72.6 s | 80 % |
| 50 x `explain()` | 13.3 s | 15 % |
| `check_artifacts` (YAML scan, ruamel, 110 files; manifest load) | 2.6 s | 3 % |
| Imports (`dbt_s_layer._commands`, metricflow) | 1.3 s | 1 % |
| Semantic views (100 x build + render) | 0.15 s | - |

Each `MetricFlowEngine.__init__` converts every semantic model to a data set and caches the SQL
of every source node (`cache_output_data_sets`): 1.4 s per engine with 100 semantic models,
against the ~30 ms Phase 2 measured on the 4-model fixtures. Phase 2 creates one engine per saved
query (02-RESEARCH finding 1: an engine that plans the same query twice renders different
subquery aliases), so the cost is (saved queries) x (semantic models).

## Finding 3: one engine, fresh planner state per query (verified)

What makes a second query differ is the planner's caches, not the engine's set-up:

- `MetricFlowEngine.__init__` resets `SequentialIdGenerator` to 10000, builds the source data
  sets and source nodes, and caches the source nodes' output data sets in a
  `DataflowNodeToSqlSubqueryVisitor`. Deterministic, and never changed by a query.
- `explain()` resets the ID generator to 0 per query (`consistent_id_enumeration=True`, the
  default), then plans with `DataflowPlanBuilder`, whose `DataflowPlanBuilderCache` (LRU caches
  of source-node recipes and metric output nodes) and node-output visitor
  (`_node_to_output_data_set`, which gains the query's nodes) carry state into the next query.

So a per-query engine can share the set-up and get its own planner: `copy.copy(base)` with a new
`DataflowPlanBuilderCache`, a new `DataflowPlanBuilder` (same source node set, source node
builder, lookup and resolver; node-output visitor `base_visitor.copy()`, which holds only the
source nodes), and a new `DataflowToSqlPlanConverter`. These are private attributes
(`_dataflow_plan_builder`, `_source_node_set`, `_node_data_set_resolver`,
`_source_node_builder`, ...) of the pinned `metricflow==0.213.0`; tests/test_stack_surface.py
must guard them like the other surfaces.

Result on the 100/50 project, same 50 export bodies (sha256 of the concatenation equal):

| Engine use | `render_exports` |
|---|---|
| One engine per saved query (Phase 2) | 29.8 s |
| One shared set-up, fresh planner per query | 5.3 s |

The existing test `test_identical_saved_queries_render_identical_sql` (two identical saved
queries) still holds, since no planner cache is shared.

The remaining 4.1 s is planning: `_find_source_node_recipe` evaluates join candidates over every
source node for each metric (about 0.04 s per metric here), and metricflow's ID generator copies
a dict per ID. Neither is ours to change.

## Finding 4: explaining saved queries in parallel (verified)

Saved queries are independent once each has its own planner, so they can be explained in
worker processes forked after the shared set-up (copy-on-write; nothing to pickle but the saved
query name in and the SQL string out). End to end in one process (import, load, set-up,
explain), 100/50 project:

| Workers | import | load | set-up | explain | total |
|---|---|---|---|---|---|
| 1 | 0.67 s | 0.70 s | 0.60 s | 4.12 s | 6.08 s |
| 2 | 0.63 s | 0.67 s | 0.53 s | 2.60 s | 4.42 s |
| 4 | 0.58 s | 0.72 s | 0.55 s | 1.21 s | 3.06 s |

Output identical across worker counts. The process is single-threaded after metricflow's imports
(`threading.active_count() == 1`), so fork is safe; Python 3.12+ would warn on fork in a
multi-threaded process. The `fork` start method exists on Linux and macOS, not on Windows: there
the render stays sequential (about 6 s). Errors must come back as messages and be raised in the
parent in sorted saved-query order, so the first error reported is the one a sequential run
reports.

With Fusion: 0.65 s parse + about 3.1 s generate is about 3.8 s on 4 cores; on 2 cores about 5 s.

## Finding 5: pre-commit and prek behaviour (verified locally, both tools)

From the pre-commit docs (pre-commit.com, "Creating new hooks") and its source
(`pre_commit/commands/run.py`, `pre_commit/xargs.py`, 4.7.0), then tried with a `repo: local`
hook under pre-commit 4.7.0 and prek 0.5.6:

- **Filtering is by file name only.** `files`/`exclude` are Python regexes (`re.search`) on the
  repo-relative path; `types` are `identify` tags (extension and shebang based). Nothing looks
  at content, so `files: \.ya?ml$` passes every staged YAML file and the hook decides itself
  whether any of them holds `semantic_models:`, `metrics:` or `saved_queries:`. That decision has
  to be cheap: a regex over the text, before importing metricflow (0.6 to 0.9 s of import).
- **Deleted files are not passed** (pre-commit filters to existing files). Deleting a semantic
  YAML file, or removing the last semantic key from one, does not trigger the hook; `check` in
  CI catches it.
- **Modification is detected by `git diff` only** (`diff_before != diff_after`, unstaged diff of
  tracked files). Verified: a hook that writes a new untracked file and exits 0 **passes** under
  both tools; one that modifies a tracked file fails with "files were modified by this hook". So
  the hook exits 1 itself when it wrote, changed or removed anything.
- **Working directory is the repository root**, even when `pre-commit run` is started in a
  subdirectory; file names are relative to it (verified, both tools). A dbt project in a
  subdirectory (`analytics/dbt_project.yml`) is found by walking up from each passed file to the
  nearest `dbt_project.yml`; `--project-dir` pins it.
- **pass_filenames and partitioning.** With `pass_filenames: true` (default) file names are
  appended after `args`. Without `require_serial: true`, pre-commit splits the files into up to
  CPU-count partitions (`max(4, ceil(n / jobs))` files each) and runs them **in parallel**: two
  hook processes would parse and write one project at once. `require_serial: true` gives one
  partition unless the command line exceeds the platform limit (128 KiB on POSIX), in which case
  the partitions run one after another; the second sees nothing to change.
- **`language: python`** installs the hook repository with `pip install .` (prek uses uv) into
  an isolated environment, so dbt-s-layer and its metricflow pin never meet the user's dbt Core
  install. The user's `dbt`/`dbtf` must be on PATH (pre-commit prepends the hook environment's
  `bin` to PATH; it ships no `dbt`, verified in the dev venv). A project venv that is not
  activated needs `--dbt path/to/dbt`.
- **Unstaged changes are stashed** while hooks run, so the parse sees the staged YAML: the
  generated files match what is committed.

## Options

| | (a) Hook parses, then generates | (b) Hook only checks | (c) Generate from stale artifacts |
|---|---|---|---|
| Criterion 1 (regenerates) | yes | no: tells the user to run two commands | wrong output: the edit is not in the artifacts |
| Criterion 2 (5 s) | Fusion: about 3.8 s with the speed-ups; Core: no (parse 5.4 s) | no faster: `check` renders everything | - |
| Criterion 3 (idempotent) | yes (byte-stable `generate`) | yes | - |
| Needs a dbt engine and a profile at commit time | yes | yes, to be useful | no |

(c) is ruled out: dbt-s-layer does not parse YAML itself (PARSE-01), so the edit would be missed.
(a) is the only option that meets criterion 1. It needs a profile because both engines need
one to parse; `--no-parse` covers a setup where something else parses first (another hook) and
CI that has no profile.

## Recommendations for planning

1. **Rendering speed (05-01).** `render_exports` builds one engine and gives each saved query
   its own planner (finding 3); with more than a handful of saved queries and the `fork` start
   method available, explains them in a process pool (finding 4). Output byte-identical, tested
   against the sequential path and the existing snapshots. `DBT_S_LAYER_JOBS=1` forces one
   process. Guard the private metricflow attributes in test_stack_surface.py.
2. **`dbt-s-layer pre-commit [FILES]...` (05-02).** Options of `generate` plus `--dbt` and
   `--no-parse`. Steps: keep the passed files that are semantic YAML (regex on the text),
   `dbt_project.yml`, or generated files (`sem__*.sql`, `exp__*.sql`, `_semantic_views.yml` in
   the output directory); group them by nearest `dbt_project.yml` (or `--project-dir`); for each
   project run `<dbt> parse` (with `--target`/`--profiles-dir` when given) in the project
   directory, then `generate`. Exit 0 and import nothing heavy when no passed file is relevant;
   exit 1 when files changed (naming them and saying to stage them) or on any error.
3. **`.pre-commit-hooks.yaml` (05-03)** with `id: dbt-s-layer`, `entry: dbt-s-layer pre-commit`,
   `language: python`, `files` for YAML and generated SQL, `require_serial: true`. README
   section with the exact snippet; an end-to-end run with pre-commit and prek on a git copy of a
   fixture; timings on the 100/50 project with both engines.

### Open risks

- The private metricflow attributes in finding 3 can move on a metricflow bump; the stack
  surface test fails first, and the byte-identity test against the fresh-engine path catches a
  silent change.
- Timings are machine-dependent: 4 cores here; 2 cores land near 5 s with Fusion.
- dbt Core users exceed 5 s on the parse alone; documented in the README.
