# Phase 6 Verification

**Status:** passed
**Date:** 2026-10-20

| # | Success criterion | Evidence |
|---|---|---|
| 1 | A `sem__<name>.sql` per semantic model that uses the metric_view materialization | Snapshots for all 9 artifact sets; compile test checks `config.materialized == 'metric_view'` for every generated node on Fusion 2.0.8 and dbt Core 1.12.5 + dbt-databricks 1.12.7 |
| 2 | Entities, dimensions and measures match the semantic YAML | test_databricks.py (mapping rows), `test_databricks_fixture_measures`; on the workspace every measure matched plain SQL (below) |
| 3 | Every upstream table is a `{{ ref() }}` | Compile test: each node's `depends_on.nodes` equals the refs of the view's tables; no `{{` or `ref(` left in compiled code |
| 4 | `dbt run --target databricks` materialises every view | Real workspace, maintainer's Mac, head 21066e9: `dbt build` PASS=15 on both engines |

## Real-workspace smoke test (2026-10-20)

Databricks SQL warehouse, OAuth M2M service principal, a fresh `dbtsl_smoke_*` schema per
engine (dropped afterwards).

- dbt Core 1.12.5 + dbt-databricks 1.12.7, `databricks_legacy`: deps, parse, generate, parse,
  check, `dbt build` PASS=15 ERROR=0. `DESCRIBE TABLE EXTENDED` works on all four views. 41 of 41
  `MEASURE()` queries match plain SQL, before and after adding fan-out rows (extra customers,
  a second store, 8 orders, 8 items, an order whose customer is missing), grouped by root and
  joined dimensions (`customer__country`, `store__region`, `order__customer__country`, ...).
- Fusion 2.0.8, `databricks_spec`: the same, `dbt build` 15/15, 40 of 40 match.

Open questions from 06-RESEARCH:

- Nested join `on` (`orders.customer_id = customers.customer_id` under `orders`): works.
- `\x24` in a double-quoted scalar decodes to `$` (DESCRIBE, view text, information_schema).
- Name resolution: in a dimension expr, a bare name that is also a measure name resolves to the
  column, so measures are exempt from the shadowing check against dimension exprs. Inside a
  measure, a dimension name wins over the column: `SUM(amount)` next to a dimension `amount`
  (`amount * 100`) gave 100 times the sum. The shadowing check refuses that case. Measure
  exprs keep the conservative rule for measure names (not tested on the workspace).
- Reserved words as field names (`order`): accepted.
- `semiadditive: last` equals SUM(amount) on the latest `ordered_at` per group (paid: 61.0
  against revenue 92.0).
- `dbt build` of the metric views succeeds on Fusion 2.0.8 (06-RESEARCH §5 was wrong).
