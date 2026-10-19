# Phase 1: Parsing Pipeline — Research

**Researched:** 2026-10-19
**Method:** hands-on. Installed dbt Fusion 2.0.8 (the `stable`/`latest` tag on `public.cdn.getdbt.com/fs/versions.json`) and dbt Core 1.12.6 + dbt-duckdb 1.11.0 in scratch environments, parsed small fixture projects with each, and loaded the artifacts with the pinned `metricflow==0.213.0`. Probe scripts are reproduced below; nothing here is from training data alone.

## Summary

| Question | Finding | Confidence |
|---|---|---|
| Which engines write `target/semantic_manifest.json`? | dbt Core 1.12.6: yes for **both** the legacy and the new YAML spec. Fusion 2.0.8: yes for the **new spec only**; legacy-spec `semantic_models:` are ignored (warning `SemanticModelDeprecated (dbt1157)`) and no semantic manifest is written. | HIGH (observed) |
| Does `parse` need warehouse access? | No. Both engines parse with dummy credentials. Fusion warns `InvalidConfig (dbt1005)`: it skips semantic-manifest validation without a `dbt_cloud.yml`. | HIGH |
| How do we load the artifact? | `metricflow_semantics.model.dbt_manifest_parser.parse_manifest_from_dbt_generated_manifest(json_str)`. Plain `PydanticSemanticManifest.parse_obj` loads, but legacy-spec manifests then fail in `SemanticManifestLookup` (`A simple metric is missing metric_aggregation_params`) because the measure-to-metric transformations have not run. | HIGH |
| Does the loaded manifest drive `explain()`? | Yes, for all three producers. With a render-only `SqlClient` stub (Snowflake renderer, every execute method raising) `explain(saved_query_name=...)` returns SQL. A sentinel written into `node_relation` survives verbatim into the `FROM` clause. | HIGH |
| `relation_name` format | Differs by engine: Fusion `ANALYTICS.MARTS.orders` (unquoted), Core `"dev"."marts"."orders"` (quoted). **Rewrite from `node_relation.database/schema_name/alias`, never by parsing `relation_name`.** | HIGH |
| Mapping a semantic model to its dbt node | `manifest.json` → `semantic_models["semantic_model.<pkg>.<name>"]` has `depends_on.nodes == ["model.<pkg>.<model>[.v<N>]"]` and `model == "ref('<model>')"` (the user's own ref expression). Present in both engines. | HIGH |
| Versioned models | Fusion resolves `ref('customers')` to `model.fx.customers.v2` and emits the semantic model. **dbt Core 1.12.6 silently drops a new-spec semantic model attached to a versioned model** (no entry in either artifact, no warning). | HIGH (observed); upstream bug not yet reported |
| Sources | Semantic models attach to models only (`model: ref(...)` / `semantic_model:` on a `models:` entry). No source-backed semantic model exists in either spec, so `__SOURCE__` has no producer. | MEDIUM (spec reading + no way to express one in either fixture) |
| Is Fusion's own `dbt sl query --compile` an alternative to MetricFlow? | No. It fails with `Missing project in dbt_cloud.yaml`: it needs dbt platform. | HIGH |
| Fusion binary name | The installer installs `dbt` and adds `alias dbtf=...` to the shell rc. `dbtf` is not a file on PATH. Fixed in Phase 0 doctor (PR #2). | HIGH |

## Implications for the plan

1. **PARSE-02 holds as written**: one artifact format, no per-spec switch. Loading must go through `parse_manifest_from_dbt_generated_manifest` so legacy manifests are normalised.
2. **The ref expression already exists.** `manifest.json` gives each semantic model's `model` field verbatim (`ref('orders')`). The sentinel map can carry that expression instead of reconstructing one from node ids, which also preserves the user's versioning intent (unpinned `ref('customers')` stays unpinned). The node id still gives package (cross-project), version and the physical relation for validation.
3. **Sentinel tokens should replace the alias only**, keeping database and schema: `ANALYTICS.MARTS.__REF__orders`. `SqlTable.from_string` splits on `.`, so the rewritten `relation_name` must be built as `"{database}.{schema}.{sentinel}"` from parts, and the token itself must not contain `.` or quotes.
4. **The Core versioned-model drop must be surfaced, not swallowed.** `manifest.json` carries no trace of it (the node's config has no `semantic_model` key in Core 1.12.6), so it can only be detected from the YAML: a `models:` entry with `semantic_model` enabled whose name has no semantic model in the artifact. The CLI-05 staleness check already walks semantic YAML, so the same walk can collect declared semantic-model names and warn on any the artifact lacks, naming the engine version.
5. **`__SOURCE__`** stays in the Phase 2 rewrite (cheap, and the requirements name it) but Phase 1 should not invent a source mapping path it cannot test. The map type allows it; no fixture produces it.

## Fixture projects used

`fx` (new spec, Fusion, Snowflake profile with dummy creds), `core_new` (same YAML, dbt Core + DuckDB), `core_legacy` (legacy `semantic_models:`/`measures:` YAML, dbt Core + DuckDB). Each has `orders` with `order`/`customer` entities, a `day` time dimension, a `revenue` simple metric, a `metricflow_time_spine` model, and saved query `revenue_by_day` with one table export. `fx`/`core_new` add a versioned `customers` model (`latest_version: 2`). New-spec YAML:

```yaml
models:
  - name: orders
    semantic_model:
      enabled: true
    agg_time_dimension: ordered_at
    columns:
      - name: order_id
        entity: {type: primary, name: order}
      - name: customer_id
        entity: {type: foreign, name: customer}
      - name: ordered_at
        granularity: day
        dimension: {type: time}
    metrics:
      - name: revenue
        type: simple
        agg: sum
        expr: amount
saved_queries:
  - name: revenue_by_day
    query_params:
      metrics: [revenue]
      group_by: ["TimeDimension('order__ordered_at', 'day')"]
    exports:
      - name: exp_revenue_by_day
        config: {export_as: table}
```

## Render-only explain probe

```python
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.snowflake import SnowflakeSqlPlanRenderer

class RenderOnly:
    sql_engine_type = SqlEngine.SNOWFLAKE
    sql_plan_renderer = SnowflakeSqlPlanRenderer()
    def query(self, *a, **k): raise RuntimeError("render only")
    execute = dry_run = query
    def close(self): pass

raw = json.load(open("target/semantic_manifest.json"))
for sm in raw["semantic_models"]:
    nr = sm["node_relation"]
    nr["relation_name"] = f'{nr["database"]}.{nr["schema_name"]}.__REF__{nr["alias"]}'
manifest = parse_manifest_from_dbt_generated_manifest(json.dumps(raw))
engine = MetricFlowEngine(SemanticManifestLookup(manifest), RenderOnly())
print(engine.explain(MetricFlowQueryRequest.create(saved_query_name="revenue_by_day")).sql_statement.sql)
```

Output (Fusion manifest):

```sql
SELECT
  DATE_TRUNC('day', ordered_at) AS order__ordered_at__day
  , SUM(amount) AS revenue
FROM ANALYTICS.MARTS.__REF__orders orders_src_10000
GROUP BY
  DATE_TRUNC('day', ordered_at)
```

## Open questions (not blocking Phase 1)

- Minimum Fusion version to support: only 2.0.8 tested. Proposal: document 2.0.8 as tested, do not hard-fail older 2.x.
- Whether to report the Core versioned-model drop upstream (dbt-labs/dbt-core). Surfaced to the project; Anentropic decides.
- Cross-project (`__XREF__`) refs need a dbt Mesh / dbt-loom fixture; deferred to Phase 2 with the rewrite itself.

## Adjacent finding (for Phases 4, 6, 7)

dbt Core 1.12.6 also writes `target/osi_document.json` (Open Semantic Interchange, `version: 0.1.1`, dialect `ANSI_SQL`) on `parse`: datasets with `source`, `primary_key`, fields (entities/dimensions with `is_time`) and metrics as rendered SQL expressions (`SUM(orders.amount)`). Fusion 2.0.8 did not write one. Worth evaluating as an input when the semantic-view adapters are researched; out of scope for Phase 1.
