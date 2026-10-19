# dbt-s-layer (WIP)

dbt semantic-layer features for dbt Core / Fusion users, no dbt Cloud required.

`dbt-s-layer` is a Python package that reads dbt semantic YAML (via `semantic_manifest.json`) and code-generates two families of dbt models:

1. **Warehouse-native semantic views** (`sem__*.sql`) — Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB semantic views
2. **Pre-aggregated exports** (`exp__*.sql`) — materialised from `saved_query` export blocks using MetricFlow's SQL rendering engine

A pre-commit hook keeps generated files in sync with YAML changes automatically.

## Status

Alpha. Under active development.

## Requirements

- Python 3.11+
- A dbt engine on `PATH` that writes `target/semantic_manifest.json` when you run `dbt parse`, either of:
  - [dbt Core](https://docs.getdbt.com/docs/core/installation-overview) 1.7 or later, which parses both the legacy and the new semantic YAML spec
  - [dbt Fusion](https://docs.getdbt.com/docs/fusion/install-fusion), which parses the new spec only

Run `dbt-s-layer doctor` to check what it finds.

## License

Apache-2.0. See [LICENSE](./LICENSE).
