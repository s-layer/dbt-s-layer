# dbt-s-layer

dbt Cloud semantic-layer features for dbt Core / Fusion users.

`dbt-s-layer` is a Python package that reads dbt semantic YAML (via `semantic_manifest.json`) and code-generates two families of dbt models:

1. **Warehouse-native semantic views** (`sem__*.sql`) — Snowflake `SEMANTIC VIEW`, Databricks metric views, DuckDB semantic views
2. **Pre-aggregated exports** (`exp__*.sql`) — materialised from `saved_query` export blocks using MetricFlow's SQL rendering engine

A pre-commit hook keeps generated files in sync with YAML changes automatically.

## Status

Alpha. Under active development.

## Requirements

- Python 3.11+
- [dbt Fusion](https://docs.getdbt.com/docs/fusion/install-fusion) binary on `PATH` (for projects using the new YAML spec)

## License

Apache-2.0. See [LICENSE](./LICENSE).
