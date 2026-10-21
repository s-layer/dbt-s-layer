# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `dbt-s-layer generate` writes a dbt model per `saved_query` export, with SQL rendered by
  MetricFlow, for every adapter MetricFlow supports. `{{ ref() }}` calls keep the models in
  the DAG.
- Snowflake semantic views (`sem__*.sql`, built by the `Snowflake-Labs/dbt_semantic_view`
  package) and Databricks metric views (`sem__*.sql`, built by dbt-databricks >= 1.12.0), one
  per semantic model.
- `dbt-s-layer check` fails when the committed models no longer match the semantic YAML.
- `dbt-s-layer doctor` checks the dbt engine, metricflow, a project's parse artifacts and the
  warehouse packages the generated models need.
- A pre-commit hook that re-parses the project and regenerates the models when a commit touches
  semantic YAML.
- `dbt-s-layer --version`.
- Both dbt Core (>= 1.7) and dbt Fusion parse artifacts are supported, and produce the same
  files.

### Known limitations

- DuckDB semantic views are not generated yet; DuckDB projects get the exports only.
- Cumulative and conversion metrics, metric filters, and some percentile kinds are left out of
  semantic and metric views (see the README).

[Unreleased]: https://github.com/s-layer/dbt-s-layer/commits/develop
