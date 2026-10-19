# 04-01 Summary: semantic view IR and builder

- `semantic_views.py`: `SemanticViewSpec` (tables, relationships, dimensions, metrics, derived
  metrics, comment) with `TableSpec`, `RelationshipSpec`, `DimensionSpec`, `MetricSpec`,
  `PercentileSpec`, `NonAdditiveSpec`, `MetricRef`, `DerivedMetricSpec`. Adapter-neutral: no
  Snowflake text in it, so Phases 6 and 7 render the same specs.
- `build_semantic_views(project)` returns one spec per semantic model, sorted, and the sorted,
  de-duplicated warnings. The join closure follows metricflow's rules (primary/unique/foreign to
  primary/unique of the same entity name, two hops, breadth first in sorted order); a second path
  to a table warns and keeps the first; a path back to the root is ignored.
- Keys only from bare-identifier entity exprs (else a warning, no key, no join). Entities become
  dimensions unless the name is taken by a dimension or (on the root) a metric. Simple metrics of
  the root only; derived and ratio metrics when every input is an emitted simple metric of one
  semantic model with no filter or offset (aliases resolved through a small SQL scanner,
  `scan_sql`, that skips literals, quoted identifiers and comments).
- Skipped with one warning each: cumulative, conversion, metric and input-measure filters,
  `window_groupings`, natural entities, approximate discrete percentiles, derived/ratio metrics
  that do not fit. `fill_nulls_with`/`join_to_timespine` warn but keep the metric. A metric named
  like a dimension of its semantic model raises `SemanticViewError` (new).
- Found while testing: metricflow rewrites `count` and `sum_boolean` to `sum` of a `CASE`, and
  `median` to `percentile` 0.5, before dbt-s-layer sees them; metricflow's validator forbids an
  entity and a dimension sharing a name anywhere in the project, so the planned "dimension wins
  over entity" test could not be built from a valid manifest and was dropped (the code path stays).
- Tests committed red first: tests/test_semantic_views.py (69).
