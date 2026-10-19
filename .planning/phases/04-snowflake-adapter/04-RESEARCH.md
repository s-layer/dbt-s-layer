# Phase 4: Snowflake Adapter - Research

**Researched:** 2026-10-19. Hands-on with dbt Core 1.12.6 + dbt-snowflake 1.12.3 and dbt Fusion 2.0.8
(scratch venv / binary), `dbt_semantic_view` v1.0.6 (cloned and installed with `dbt deps`), metricflow
0.213.0 (the project's pinned venv), and the live Snowflake docs fetched on 2026-10-19.
**Confidence:** HIGH for anything marked *verified* (run locally, output inspected) or quoted from a
source; MEDIUM for anything marked *inferred*. Nothing was run against a real Snowflake account, so
Snowflake's acceptance of a given DDL body is never verified here (see Open risks).

## Summary

1. `dbt_semantic_view` (hub `Snowflake-Labs/dbt_semantic_view`, latest **1.0.6**, Apache-2.0) is a thin
   passthrough: the model body is the raw `CREATE SEMANTIC VIEW` clause list (`TABLES(...)
   RELATIONSHIPS(...) FACTS(...) DIMENSIONS(...) METRICS(...) COMMENT=...`), and the materialization
   prepends `create or replace semantic view <relation>`. There is no config schema for
   tables/dimensions/metrics. **We generate DDL text, not config.** `{{ ref() }}` / `{{ source() }}`
   inside `TABLES` give correct DAG edges on both engines (verified).
2. The grammar is stable and documented; FACTS must precede DIMENSIONS; at least one DIMENSION or
   METRIC is required; relationships must reference a `PRIMARY KEY` or `UNIQUE` column set.
3. After metricflow's transformations, **both YAML specs present every aggregation as a simple
   metric** with `metric_aggregation_params` (`agg`, `semantic_model`, `expr`); new-spec semantic
   models have `measures: []`. Generate METRICS from simple metrics, not from measures (verified).
4. One `sem__<name>` per semantic model works, but a single-table view loses every join MetricFlow
   allows. Recommendation: each view holds its semantic model **plus the semantic models reachable
   through its entities in MetricFlow's join direction (max 2 hops)**, with RELATIONSHIPS between them,
   and only the root model's metrics.
5. `dbt compile` works without a Snowflake account: Core needs `--no-populate-cache`; Fusion 2.0.8
   compiles offline but warns `dbt0101` on the non-SELECT body unless the model sets
   `static_analysis='off'` (verified; Core accepts that key silently).
6. Question 5: dbt Core 1.12.6 on a Snowflake target does **not** treat a case-only difference in
   alias or schema as a clash; parse succeeds with both models (verified). dbt-s-layer's
   case-insensitive clash check remains necessary on Snowflake too.

## 1. Snowflake `CREATE SEMANTIC VIEW` grammar

Source: https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view (fetched 2026-10-19;
syntax blocks copied from the page).

```
CREATE [ OR REPLACE ] SEMANTIC VIEW [ IF NOT EXISTS ] <name>
  TABLES ( logicalTable [ , ... ] )
  [ RELATIONSHIPS ( relationshipDef [ , ... ] ) ]
  [ FACTS ( factExpression [ , ... ] ) ]
  [ DIMENSIONS ( dimensionExpression [ , ... ] ) ]
  [ METRICS ( { metricExpression | windowFunctionMetricExpression } [ , ... ] ) ]
  [ COMMENT = '<comment_about_semantic_view>' ]
  [ MAX_STALENESS = <integer> ]
  [ AI_SQL_GENERATION '<instructions>' ]  [ AI_QUESTION_CATEGORIZATION '<instructions>' ]
  [ AI_VERIFIED_QUERIES ( verifiedQuery [ , ... ] ) ]
  [ [ WITH ] TAG ( <tag_name> = '<tag_value>' [ , ... ] ) ]
  [ COPY GRANTS ]

logicalTable ::=
  [ <table_alias> AS ] <table_name>
  [ PRIMARY KEY ( <primary_key_column_name> [ , ... ] ) ]
  [ UNIQUE ( <unique_column_name> [ , ... ] ) [ ... ] ]
  [ CONSTRAINT [ <constraint_name> ] DISTINCT RANGE BETWEEN <start_column> AND <end_column> EXCLUSIVE ]
  [ WITH SYNONYMS [ = ] ( '<synonym>' [ , ... ] ) ]
  [ COMMENT = '<comment_about_table>' ]
  [ [ WITH ] TAG ( ... ) ]

relationshipDef ::=
  [ <relationship_identifier> AS ]
  <table_alias> ( <column_name> [ , ... ] )
  REFERENCES
  <ref_table_alias> [ ( [ ASOF ] <ref_column_name> [ , ... ] | BETWEEN <start_column> AND <end_column> EXCLUSIVE ) ]

factExpression ::=
  [ { PRIVATE | PUBLIC } ] <table_alias>.<fact> [ LABELS = ( FILTER ) ] AS <sql_expr>
  [ WITH SYNONYMS [ = ] ( '<synonym>' [ , ... ] ) ] [ [ WITH ] TAG ( ... ) ] [ COMMENT = '<comment>' ]

dimensionExpression ::=
  [ PUBLIC ] <table_alias>.<dimension> [ LABELS = ( FILTER ) ] AS <sql_expr>
  [ WITH SYNONYMS ... ] [ [ WITH ] TAG ( ... ) ] [ COMMENT = '<comment>' ]
  [ WITH CORTEX SEARCH SERVICE <search_service_name> [ USING <search_service_column_name> ] ]

metricExpression ::=
  [ { PRIVATE | PUBLIC } ] <table_alias>.<metric>
  [ USING ( <relationship_name> [ , ... ] ) ]
  [ NON ADDITIVE BY ( <dimension> [ { ASC | DESC } ] [ NULLS { FIRST | LAST } ] [ , ... ] ) ]
  AS <sql_expr>
  [ WITH SYNONYMS ... ] [ [ WITH ] TAG ( ... ) ] [ COMMENT = '<comment>' ]

windowFunctionMetricExpression ::=
  [ { PRIVATE | PUBLIC } ] <table_alias>.<metric> AS
  <window_function>( <metric> ) OVER (
    [ PARTITION BY { <exprs_using_dimensions_or_metrics> | EXCLUDING <dimensions> } ]
    [ ORDER BY <exprs_using_dimensions_or_metrics> [ ASC | DESC ] [ NULLS { FIRST | LAST } ] [, ...] ]
    [ <windowFrameClause> ] )
```

Rules that constrain the generator (same page unless noted; "validation" =
https://docs.snowflake.com/en/user-guide/views-semantic/validation-rules; "SQL guide" =
https://docs.snowflake.com/en/user-guide/views-semantic/sql; "querying" =
https://docs.snowflake.com/en/user-guide/views-semantic/querying):

- "The order of the clauses is important. For example, you must specify the FACTS clause before the
  DIMENSIONS clause." Forward references between expressions are allowed.
- "you must define at least one dimension or metric". TABLES is the only mandatory clause.
- Alias: "If you specify an alias, you must use this alias when referring to the logical table".
- Relationship target columns must be "a column identified with the PRIMARY KEY or UNIQUE constraint";
  `REFERENCES orders` with no column list uses the target's key (TPC-H example: `orders (o_custkey)
  REFERENCES customers`). Don't repeat a PRIMARY KEY column in UNIQUE.
- Keys must be "physical base table columns or expressions defined in logical tables that directly
  refer to a base table column (for example, t1.fact AS t1.col)" (validation). A computed entity
  `expr` cannot be a key.
- No circular relationships "even through transitive paths"; no self-references; multiple paths
  between two tables are allowed but then those tables "cannot refer to each other's semantic
  expressions" unless `USING` picks the path (validation). Relationships are transitive.
- Metrics: "A metric that is not a derived metric must use an aggregate function." Metrics may refer
  to other metrics at equal or lower granularity without aggregation (validation).
- Derived metrics: "omit table_alias. from the name"; may combine metrics from any logical table;
  can't share a name with a regular metric (SQL guide, "Defining derived metrics").
- `NON ADDITIVE BY`: rows are sorted by the listed dimensions and "the values from the last rows in the
  sorted order are aggregated"; ASC (default) = latest. A non-additive metric can only be referenced by
  derived metrics (SQL guide).
- Dimensions are always public (`PRIVATE` not allowed); facts and metrics can be PRIVATE.
- Synonyms are informational only.
- Names: start with a letter, no special chars unless double-quoted; "Identifiers enclosed in double
  quotes are also case-sensitive."
- Name resolution: "If both a semantic expression and a column have the same name, references to that
  name resolve to the semantic expression", except inside that expression's own definition
  (validation). This matters, see Open risks.
- The same element name on two logical tables is legal; bare names in queries must then be qualified
  ("Ambiguous column name" otherwise; querying, lines on bare vs dot-notation names).
- Preview-only (avoid): range `CONSTRAINT ... DISTINCT RANGE`, relationship `BETWEEN`, metric `USING`.
  The page marks each "Preview Feature — Open". ASOF, window metrics and NON ADDITIVE BY are not marked.
- `CREATE OR ALTER` exists; it unsets any property absent from the statement.
- A logical table may also be "a SQL query" (SQL guide, "Defining the logical tables"); not needed.

## 2. The `dbt_semantic_view` package

Sources: https://github.com/Snowflake-Labs/dbt_semantic_view (cloned at `2fbbd43`, 2026-09-14),
`https://hub.getdbt.com/api/v1/Snowflake-Labs/dbt_semantic_view.json` and `.../1.0.6.json`.

| Fact | Value | Source |
|------|-------|--------|
| Hub name | `Snowflake-Labs/dbt_semantic_view` | hub API |
| Latest | **1.0.6** (tag 2026-07-22); versions 0.1.0 .. 1.0.6 | hub API, `git tag` |
| License | **Apache-2.0** (`LICENSE`, SPDX headers on every macro) | repo |
| `require-dbt-version` | `[">=1.0.0", "<3.0.0"]` (widened from `<2.0.0` in PR #14, released in 1.0.4, "update-fusion-compatibility-version") | `dbt_project.yml:22`, commit `d43a949` |
| Fusion | hub `fusion_compatibility`: `parse_compatible: true` on Fusion `2.0.0-preview.205`, `manually_verified_compatible: false`. We verified parse + compile on Fusion 2.0.8 (below). | hub API 1.0.6 |
| Adapter | Snowflake only (`{% materialization semantic_view, adapter='snowflake' %}`) | `macros/materializations/semantic_view.sql` |
| Note | `dbt_project.yml` still says `version: '1.0.1'` at every later tag; use the hub/tag version | repo |

How a model uses it (README "Usage in your dbt project"; `integration_tests/models/semantic_view_basic.sql`):

```sql
{{ config(materialized='semantic_view') }}

TABLES(t1 AS {{ ref('base_table') }}, t2 as {{ source('seed_sources', 'base_table2') }})
DIMENSIONS(t1.count as value, t2.volume as value)
METRICS(t1.total_rows AS SUM(t1.count), t2.max_volume as max(t2.volume))
COMMENT='test semantic view'
```

What the materialization does (`macros/relations/semantic_view/create.sql`, 1.0.6):

- Body = rendered model SQL; statement = `create or replace semantic view {{ relation }}\n  {{ sql }}`
  (or `create or alter ...` with `create_or_alter=true`). The relation is
  `api.Relation.create(identifier=model['alias'], schema=schema, database=database, type='view')`, so
  standard `alias` / `schema` / `database` config apply.
- Config keys: `copy_grants` (bool), `create_or_alter` (bool), `max_staleness` (str, appended as
  `MAX_STALENESS = '<v>'`), `sv_materializations` (YAML string; needs `max_staleness`). It raises if
  `max_staleness` is in config **and** the substring `max_staleness` appears anywhere in the body
  (lower-cased); a generated description containing that word would trip it (*inferred*, from the
  `'max_staleness' in (sql | lower)` test).
- README: "This package is a direct passthrough to Snowflake's SQL layer"; `persist_docs` is not
  supported, inline `COMMENT =` is the way to document.
- Unit-test support (`sv_aware_ref`, `sv_def__<model>` macros) is on `main` (`ff8a6b2`, 2026-09-09)
  but **not in any release**. Not relevant to generation.

Consequences for us: there is no tables/dimensions/metrics config schema to target (ARCHITECTURE.md's
`config(tables=..., dimensions=...)` assumption is wrong). dbt-s-layer renders the clause list itself,
which also means Phases 6/7 can share an adapter-neutral intermediate model but not output.

## 3. Mapping MetricFlow → Snowflake semantic view

### What the loaded manifest actually contains (verified)

Probe: `/tmp/claude-0/-home-user/66fac691-d071-5c17-8743-df4ac93b6a94/scratchpad/scripts/inspect_loaded.py`
(loads each artifact set through `load_project`, prints elements). Findings:

- New spec (Core and Fusion): `semantic_model.measures == []`. Every aggregation is a `simple` metric
  with `type_params.expr` and `type_params.metric_aggregation_params` = `{agg, semantic_model,
  agg_time_dimension, agg_params, non_additive_dimension}`.
- Legacy spec after `transform_dbt_generated_manifest`: measures remain, **and** each simple metric
  also carries `metric_aggregation_params` (`agg=sum, semantic_model=orders`) and `expr=amount`
  (`agg_time_dimension` None there; it lives on `defaults`). So one code path, keyed on simple metrics,
  covers both specs.
- metricflow rewrites some aggregations during transformation: `count` with a plain column becomes
  `sum` of `CASE WHEN store_id IS NOT NULL THEN 1 ELSE 0 END` (fusion_export `store_count`;
  `convert_count.py`), `sum_boolean` becomes `sum` of `CASE WHEN <expr> THEN 1 ELSE 0 END`
  (`boolean_aggregations.py:66-79`). Both are still valid Snowflake aggregates, so no special case.
- `AggregationType` = sum, min, max, count_distinct, sum_boolean, average, percentile, median, count.
  `EntityType` = foreign, natural, primary, unique. `MetricType` = simple, ratio, cumulative, derived,
  conversion.
- Fusion fills `dimension.expr` / `entity.expr` with the column name; Core leaves dimension `expr`
  null. Use `expr or name`.

Prior art in the pinned wheel (Apache-2.0, `metricflow-0.213.0.dist-info` `License-Expression`):
`metricflow/converters/msi_to_osi.py` converts a manifest to OSI. It drops natural entities, private
and conversion metrics, flags cumulative as lossy, derives relationships from shared entity names with
the FK side as "from" (`_relationship_direction`, `_build_relationships`), and maps aggregations
(`_build_agg_expression`: median → `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x)`, metric filter →
`CASE WHEN <filter> THEN x END` inside the aggregate). Useful as a reference; we should not import it
(it is not a stable surface and targets OSI, not DDL).

### Shape: one view per semantic model, with its join closure

A semantic view with one table can have no RELATIONSHIPS, so `revenue` by `customer__country` (a
plain MetricFlow query) would be impossible in `sem__orders`. Options:

| Shape | Pros | Cons |
|-------|------|------|
| A. One table per view (roadmap literal) | Simplest; no relationship validation risk | Loses all joins; Cortex Analyst sees isolated tables |
| **B. Root model + join closure (recommended)** | Matches MetricFlow: a metric on `orders` can group by dims of models reachable through its entities; still one `sem__<name>` per semantic model (SEMVW-01 holds) | Dimensions of shared models repeat across views (generated, so harmless); more DDL to get right |
| C. One project-wide view | Closest to Snowflake's intended use | Breaks SEMVW-01 / file-per-model ownership; one bad model breaks all; multi-path ambiguity across the whole graph |

Recommend **B**. Closure rule, mirroring metricflow (`semantic_model_join_evaluator.py:33-44`, valid
joins; `MAX_JOIN_HOPS = 2`, line 16): from the root, follow each entity whose type is foreign,
primary, unique or natural to another semantic model that has the same entity name as **primary or
unique** (the "one" side); repeat from the reached model up to 2 hops; BFS in sorted order, skip
visited models (Snowflake forbids cycles). Each edge becomes `child (fk_cols) REFERENCES parent`. If
two different paths reach the same model, keep the first in BFS order and warn (multi-path needs the
preview `USING`). Metrics: only those whose `metric_aggregation_params.semantic_model` is the root.
Dimensions: every table's dimensions (qualified by its alias). Fixture check: in `new_spec`,
`order_items` (FK `order`) reaches `orders` (PK `order`); `orders`' FK `customer` reaches nothing
(customers' primary entity is `cust`), so `sem__orders` is single-table and `sem__order_items` has
two tables. In `export_spec`, `sem__order_items` reaches orders, then customers and stores (2 hops).

If the planner prefers A for Phase 4, B is a pure extension later (same IR, more tables); C is the
one shape that would change SEMVW-01.

### Element mapping

| MetricFlow | Snowflake | Notes |
|-----------|-----------|-------|
| semantic model | logical table `<sm_name> AS {{ ref(...) }}` | alias = lowercased semantic model name; ref from Phase 1/2 `RefTarget` + `sentinels.render_ref` (package, version pinned as for exports) |
| `description` | `COMMENT = '...'` on the table; root's also on the view | README says inline COMMENT is the only docs path |
| entity primary | `PRIMARY KEY (<expr or name>)` | only if expr is a bare identifier; else warn, no key |
| entity unique | `UNIQUE (<col>)` | same; skip if it equals the PK column |
| entity foreign | relationship source column(s) | relationship only when the target has the entity as PK/UNIQUE |
| entity natural | none | SCD-2 / validity params; ASOF could approximate later. Warn and skip (as OSI converter does) |
| entities as group-by | `DIMENSIONS (t.<entity> AS <col>)` | MetricFlow lets you group by an entity; recommend emitting primary/unique/foreign entities as dimensions unless a dimension already has that name (*decision for planning*) |
| categorical dimension | `t.<name> AS <expr or name>` | |
| time dimension | `t.<name> AS <expr or name>` | Snowflake has no granularity; declared `time_granularity` is the column's grain. Optional later: `<name>__month AS DATE_TRUNC('month', ...)` variants. `validity_params` → skip (natural entity case) |
| `is_partition`, `datatype`, `label` | none / `WITH SYNONYMS ('<label>')` | label as synonym is optional nice-to-have |
| simple metric sum/min/max/average | `t.<metric> AS SUM/MIN/MAX/AVG(<expr>)` | |
| count_distinct | `COUNT(DISTINCT <expr>)` | |
| count, sum_boolean | arrive as `sum` of `CASE WHEN` (above) | |
| median | `MEDIAN(<expr>)` or `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY <expr>)` | aggregate accepted in metrics: *inferred* (docs say "must use an aggregate function"; no list) |
| percentile | `PERCENTILE_CONT(p)` / `PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY ...)`, approx continuous → `APPROX_PERCENTILE(expr, p)`, approx discrete → unsupported | copy metricflow's Snowflake renderer rules (`metricflow/sql/render/snowflake.py:54-78`); semantic-view acceptance *inferred* |
| metric `filter` | `SUM(CASE WHEN <sql> THEN <expr> END)` | needs the `{{ Dimension(...) }}` filter rendered to SQL against this view's columns; recommend **skip with warning** in Phase 4 |
| `non_additive_dimension` (name, window_choice, window_groupings) | `NON ADDITIVE BY (<dim> ASC)` for `max`, `DESC` for `min` | only when `window_groupings` is empty; MetricFlow groups the "last row" per entity, Snowflake per queried dims (*inferred* difference). Else warn and skip the metric |
| `agg_time_dimension` | none | metric_time has no Snowflake analogue; drop silently |
| `fill_nulls_with`, `join_to_timespine` | none | warn, emit the metric without it |
| private simple metric (`is_private`) | `PRIVATE t.<metric>` | |
| derived metric | view-scoped derived metric `name AS <expr over metrics>` | possible when every input is a simple metric in this view, no `offset_window`/filter/alias. Recommend deferring to a follow-up; skip with warning |
| ratio | derived `num / NULLIF(den, 0)` | same as derived; defer |
| cumulative | (window metrics are partitioned by queried dims, not a time spine) | skip with warning; exports cover it |
| conversion | none | skip with warning |

Skips are warnings, never errors: a project with cumulative metrics is normal and must still generate.
Emit one WARN per skipped element, with its name and reason, sorted (deterministic output).

### Identifiers and literals

- metricflow lower-cases element names. Emit them unquoted (Snowflake folds to upper, consistent with
  dbt-snowflake's default `quoting.identifier: false`). If a name is a Snowflake reserved keyword
  (https://docs.snowflake.com/en/sql-reference/reserved-keywords; e.g. `ORDER`, `GROUP` "Reserved by
  ANSI"), emit `"ORDER"` (quoted upper case = the same identifier Snowflake folds to). The `new_spec`
  fixture has an entity named `order`; if entities become dimensions this case is real.
- String literals (COMMENT, SYNONYMS): Snowflake single-quoted strings interpret backslash escapes, so
  escape `\` → `\\` and `'` → `''`, then run `sentinels.escape_jinja` over the whole body (02-RESEARCH
  finding 5: expressions and descriptions can contain `{{`).
- `expr`s are copied verbatim (they are already Snowflake SQL for a Snowflake project).

## 4. Compiling without a Snowflake account (verified)

Setup: scratch venv `uv venv -p 3.12` + `uv pip install dbt-core==1.12.6 dbt-snowflake` (installed
dbt-snowflake **1.12.3**); Fusion 2.0.8 from
`https://public.cdn.getdbt.com/fs/cli/fs-v2.0.8-x86_64-unknown-linux-gnu.tar.gz`. Project: copy of
`tests/fixtures/projects/new_spec` (its `snowflake` target has dummy credentials) plus hand-written
`models/semantic/sem__{orders,order_items,customers}.sql` in the recommended shape.

| Step | Result |
|------|--------|
| `dbt deps` with `package: Snowflake-Labs/dbt_semantic_view, version: 1.0.6` | **fails here**: hub metadata resolves, but the tarball URL is `codeload.github.com` (`downloads.tarball`, `dbt/deps/base.py:131`), which this sandbox's proxy answers 403. Environment-only; the hub CDN tarball itself downloads fine with curl. |
| `dbt deps` with `git: https://github.com/Snowflake-Labs/dbt_semantic_view.git, revision: v1.0.6` | works (pinned `9851547`). Use this form in test fixtures. |
| Core `dbt parse --target snowflake` | rc 0. `sem__order_items` depends_on `model.fx.order_items`, `model.fx.orders`; `sem__customers` → `model.fx.customers.v2` (from `ref('customers', v=2)`); materialized `semantic_view`. (Core still drops the versioned model's own semantic model, as known.) |
| Core `dbt compile --target snowflake` | **rc 2**: `290404 (08001): 404 Not Found: post dummy.snowflakecomputing.com/.../login-request` (cache population connects). |
| Core `dbt compile --no-populate-cache` | **rc 0**, compiled body has `orders AS ANALYTICS.MARTS.orders`, `order_items AS ANALYTICS.MARTS.Order_Items`. |
| Fusion `dbt parse` | rc 0, same depends_on, `relation_name` `ANALYTICS.MARTS.sem__orders`. |
| Fusion `dbt compile` | rc 0, offline, but 3× `[warning] [SyntaxInvalid (dbt0101)]: extraneous input 'TABLES' ...` with a backtrace: Fusion's static analysis parses the body as a query. |
| Fusion compile with `config(materialized='semantic_view', static_analysis='off')` | rc 0, no dbt0101 warnings. Core 1.12.6 accepts the extra key with no warning (rc 0). **Emit it.** |
| Package absent (no `packages.yml`) | Core parse and compile both rc 0. The missing materialization only fails at `dbt run`. |

So SC4 ("`dbt compile --target snowflake` succeeds") is testable in CI with
`dbt compile --no-populate-cache` (Core) or `dbt compile` (Fusion), dummy credentials, no account.
It proves Jinja/refs/DAG only, not Snowflake's DDL validation.

Because the missing package is silent until `dbt run`, check at generate time: manifest.json `macros`
contains `macro.dbt_semantic_view.materialization_semantic_view_snowflake` when the package is
installed (verified on both engines). `DbtManifest` would need a `macros` key set (names only).

## 5. Case-only relation clash on Snowflake (verified)

Fixture approach from 03-RESEARCH finding 2, on the `snowflake` target of a `new_spec` copy, dbt Core
1.12.6 + dbt-snowflake 1.12.3, two plain models in `models/case/`:

| a_model | b_model | `dbt parse` |
|---------|---------|-------------|
| `schema='reporting', alias='revenue_daily'` | `schema='reporting', alias='REVENUE_DAILY'` | **rc 0**, no warning. relation_names `ANALYTICS.MARTS_reporting.revenue_daily` and `ANALYTICS.MARTS_reporting.REVENUE_DAILY` |
| same | `schema='Reporting', alias='revenue_daily'` | **rc 0**, no warning |
| same | `schema='reporting', alias='revenue_daily'` | rc 2: `dbt found two resources with the database representation "ANALYTICS.MARTS_reporting.revenue_daily"` naming both nodes |

With dbt-snowflake's default (identifiers unquoted) both models build `ANALYTICS.MARTS_REPORTING.
REVENUE_DAILY`. dbt Core's check is case-sensitive on Snowflake too, so dbt-s-layer's case-insensitive
comparison (output.py `_relation_key`) is the only guard and should stay (now confirmed for the
Snowflake adapter, not only DuckDB/Fusion).

## Recommendations for planning

### Module layout

- `src/dbt_s_layer/semantic_views.py` — adapter-neutral IR and builder:
  `SemanticViewSpec(name, root, tables: tuple[TableSpec], relationships, dimensions, metrics, comment)`,
  `TableSpec(alias, ref_jinja, primary_key, unique_keys, comment)`, `RelationshipSpec(name, child,
  child_cols, parent)`, `DimensionSpec(table, name, expr, comment)`, `MetricSpec(table, name, agg, expr,
  agg_params, non_additive, private, comment)`, plus `Skipped(element, reason)`. Built from
  `SemanticProject` + `build_ref_map`; join closure lives here. Phases 6 and 7 reuse it.
- `src/dbt_s_layer/snowflake.py` — `render_snowflake(spec) -> str` (clause order TABLES,
  RELATIONSHIPS, FACTS (none for now), DIMENSIONS, METRICS, COMMENT), identifier quoting and literal
  escaping.
- `semantic_views.render_semantic_views(project, *, adapter) -> SemanticViewResult(models, warnings)`
  with a dispatch dict `{"snowflake": render_snowflake}`. Other adapters: no `sem__` files and one
  WARN ("semantic views are not supported for <adapter> yet; only exports were generated"), not an
  error, so DuckDB fixtures keep working until Phase 7.
- `SemanticViewModel(semantic_model, file_name='sem__<name>.sql', alias='sem__<name>', schema=None,
  body)`. Do not alias it to the semantic model's name: it would collide with the model it reads in
  the same schema.

### Generated file (new_spec fixture, `sem__order_items.sql`; compile-verified on both engines)

```sql
-- Generated by dbt-s-layer from semantic model 'order_items'. Do not edit: change the semantic YAML and run `dbt-s-layer generate`.
{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  order_items AS {{ ref('order_items') }}
    PRIMARY KEY (order_item_id),
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
)
RELATIONSHIPS (
  order_items_to_orders AS order_items (order_id) REFERENCES orders
)
DIMENSIONS (
  order_items.ordered_at AS ordered_at,
  orders.ordered_at AS ordered_at
)
METRICS (
  order_items.items_sold AS SUM(quantity)
)
```

and `sem__orders.sql` is the single-table case (`orders AS {{ ref('orders') }} PRIMARY KEY
(order_id)`, `orders.ordered_at AS ordered_at`, `orders.revenue AS SUM(amount) WITH SYNONYMS =
('Revenue')` if labels become synonyms). Versioned/package models render as `{{ ref('customers', v=2)
}}` / `{{ ref('fx_dep', 'stores') }}` via `render_ref`. If entities are emitted as dimensions, add
`order_items.order_item AS order_item_id`, `order_items."ORDER" AS order_id`, etc. The header line is
inside the DDL after `create or replace semantic view X` (the package puts the body after the
relation); a `--` comment line is valid SQL there (*inferred*; not run on Snowflake).

### Plugging into `render_output` and the clash check

- `render_output` renders exports and semantic views, merges files, and fails on duplicate file names
  (cannot happen across prefixes, but keep the guard). Add `_semantic_header(model)` with the same
  `_SQL_MARK` prefix so `_is_generated` / ownership work unchanged.
- `_schema_file` takes both kinds: `sem__<name>` entries with the semantic model's `description` or
  `Semantic view of semantic model '<name>'.` (sorted by name, as now).
- Generalise `_check_clashes` over a small protocol (`file_name`, `alias`, `schema`, `label`) so sem
  models get the same name check (`sem__orders` vs a user model of that name) and relation check
  (folder `+schema`, alias `sem__<name>`, case-insensitive). Also check sem aliases against export
  aliases (an export aliased `sem__orders` is otherwise unchecked).
- `render_output` needs to return warnings (skipped elements, unsupported adapter, package missing);
  `_commands._render` prints them as `WARN ...`, as it already does for `check_artifacts`.
- Run `sentinels.postflight` on each body (no sentinel can appear, but a semantic model `expr`
  containing `__REF__` would be caught, consistent with exports; preflight already scans the
  semantic manifest).
- `doctor`: report whether `dbt_semantic_view` is installed (manifest macro key) when the target
  adapter is Snowflake.

### Tests

- Snapshot tests of the rendered body for `fusion_new`, `core_new`, `core_legacy` (identical bodies
  modulo relation names) and `fusion_export` (2-hop closure, versioned + package refs).
- Unit tests per mapping row: each agg, percentile variants, non-additive, private, every skip
  reason, reserved-word quoting, comment escaping (`'`, `\`, `{{`).
- A fixture project (copy of `export_spec` with `packages.yml` using the `git:` form at `v1.0.6`) for
  an optional integration test: `dbt compile --no-populate-cache --target snowflake` (Core) and
  `dbt compile` (Fusion) succeed and every `sem__*` node's `depends_on` matches the ref map (SC3, SC4).
  Commit `package-lock.yml`; `regen_fixtures.py` already runs `dbt deps`.

### Open risks

1. **No Snowflake validation.** Everything Snowflake would reject (key rules, multi-path, aggregate
   functions in metrics, name shadowing) is only checked against docs. A real-account smoke run before
   v1 is the only full proof; until then mark the adapter "verified to compile".
2. **Name shadowing.** Snowflake resolves a bare name to a semantic expression before a column. A
   dimension `amount AS amount * 100` would silently change `SUM(amount)` in a metric. MetricFlow
   resolves exprs against physical columns. Mitigation: error (or warn) when an element's name equals
   an identifier used in another element's expr and its own expr is not exactly that column.
3. **Non-additive semantics differ** when MetricFlow `window_groupings` are set (skip those) and
   possibly in tie/NULL handling (*inferred*).
4. **Join-closure duplication**: a model reachable by two paths (diamond) needs `USING` (preview);
   we keep the first path and warn, so a dimension may be reachable by a path the user did not intend.
5. **Package drift**: `dbt_semantic_view` is a passthrough with few config knobs; 1.0.x has been stable
   in surface. Pin `>=1.0.6,<1.1.0` in docs. Fusion compatibility is hub-auto-checked only
   (`manually_verified_compatible: false`); we verified 2.0.8 parse/compile.
6. **`max_staleness`**: if users add `max_staleness` config via `dbt_project.yml`, a generated COMMENT
   containing "max_staleness" makes the package raise. Low likelihood; document.
7. Phase-scope decisions to settle in planning: shape A vs B; entities-as-dimensions; labels as
   synonyms; whether a missing package is a WARN or an error at generate time (recommend WARN: the files
   are inert until `dbt run`).
