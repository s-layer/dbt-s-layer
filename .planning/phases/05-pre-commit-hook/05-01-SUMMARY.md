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
