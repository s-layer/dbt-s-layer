# Phase 6 Verification

**Status:** pending the real-workspace smoke test (success criterion 4)
**Date:** 2026-10-19

| # | Success criterion | Evidence |
|---|---|---|
| 1 | A `sem__<name>.sql` per semantic model that uses the metric_view materialization | Snapshots for all 9 artifact sets; compile test checks `config.materialized == 'metric_view'` for every generated node on Fusion 2.0.8 and dbt Core 1.12.5 + dbt-databricks 1.12.7 |
| 2 | Entities, dimensions and measures match the semantic YAML | test_databricks.py (mapping rows), `test_databricks_fixture_measures` on both fixture sets |
| 3 | Every upstream table is a `{{ ref() }}` | Compile test: each node's `depends_on.nodes` equals the refs of the view's tables; no `{{` or `ref(` left in compiled code |
| 4 | `dbt run --target databricks` materialises every view | Pending: smoke test on the maintainer's Mac (dbt Core; Fusion 2.0.8 is expected to fail, 06-RESEARCH §5) |

## Open questions for the smoke test (06-RESEARCH)

- Nested join `on` syntax (`orders.customer_id = customers.customer_id` under `orders`).
- `\x24` in a double-quoted scalar is decoded to `$` by the metric view YAML parser.
- A field named like a source column, and reserved words as field names.
- `dbt run` fails on Fusion 2.0.8 and succeeds on dbt Core.
- `semiadditive: last` gives MetricFlow's numbers for `latest_revenue`.
