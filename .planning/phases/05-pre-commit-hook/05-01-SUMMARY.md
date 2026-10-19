# 05-01 Summary: export rendering speed

- `render_exports` builds one `MetricFlowEngine` (`_SharedEngine`) and gives each saved query
  an engine copy with a fresh planner: new `DataflowPlanBuilderCache`, a new
  `DataflowPlanBuilder` on the shared source node set with a copy of the source-node visitor,
  and a new `DataflowToSqlPlanConverter`. The set-up (every semantic model's data set and source
  node SQL) is built once instead of once per saved query.
- Saved queries are explained in a forked `multiprocessing` pool when there are at least 8,
  more than one CPU and the `fork` start method (Linux, macOS): `worker_count` = min(CPUs, 8,
  saved queries). Workers inherit the set-up through a module global set before forking and
  return SQL or an error message; the parent raises the first error in sorted saved-query
  order, as a sequential run does. `render_exports(..., jobs=)` and `DBT_S_LAYER_JOBS`
  override the count (a non-positive or non-integer value is an `ExportRenderError`).
- Output is byte-identical: a test compares every body with Phase 2's engine-per-saved-query
  rendering on all four export artifact sets and three dialects, with 1 and 4 workers; the
  100/50 project's committed output reads "up to date" after the change.
- `generate` on the 100/50 project: 34 s before, 3.4 to 4.6 s after (4 cores, machine load
  varies); 7.2 s with `DBT_S_LAYER_JOBS=1`.
- `scripts/make_scale_project.py` writes the 100/50 project (committed with the research).
- test_stack_surface.py guards the private metricflow attributes the shared set-up uses.
- Tests committed red first: test_exports.py (+48 cases: one engine per render, bodies against
  the reference, pool vs one process, identical saved queries with 1 and 4 workers, first error
  in a pool, bind parameters in a pool, worker count, bad `DBT_S_LAYER_JOBS`, `jobs=0`).
  The existing `test_identical_saved_queries_render_identical_sql` is unchanged and still
  passes.

## Review round 1 (PR #8)

- The pool is now `concurrent.futures.ProcessPoolExecutor(mp_context=get_context("fork"))`
  instead of `multiprocessing.Pool`: `Pool.map` waited forever when a worker died (it replaces
  the process but the task is lost). A dead worker now raises `BrokenProcessPool`, turned into
  an `ExportRenderError` that suggests `DBT_S_LAYER_JOBS=1`. Chosen over a serial fallback: a
  worker killed for memory would likely take the parent down too, and a crash in metricflow
  would repeat. With `fork` the executor starts every worker before its manager thread.
- `_explain_all` stays serial while another thread runs (`threading.active_count() > 1`):
  a forked child inherits locks other threads held.
- macOS: kept `fork`. At the fork, the process has one thread (`MainThread`) after loading
  the project, importing the CLI and building the engine; no `numpy`, `_scproxy`, `ctypes`
  or `urllib.request` is imported (the usual sources of Objective-C / system-framework state
  that make fork unsafe on darwin). Native extensions loaded: pydantic_core, rapidfuzz,
  markupsafe, PyYAML's `_yaml`, and stdlib modules (`_ssl`, `_hashlib`, `_uuid`, ...).
  On Python 3.13, the pool tests under `-X dev -W always::DeprecationWarning` emit no
  "multi-threaded ... fork()" warning. (`-W error::DeprecationWarning` cannot detect it:
  CPython clears a warning raised as an error inside `os.fork()`.) Linux only so far; to be
  confirmed on a Mac.
- Tests committed red first (test_exports.py, +2): a worker calling `os._exit` (SIGALRM
  deadline: on the old code it timed out after 60 s), and no fork with a second thread
  running (old code: four worker pids).
