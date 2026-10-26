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
  files. Dimension `meta` reads the same from either engine: a column's `config.meta` is merged
  into its dimension's, as dbt Core does, although Fusion leaves it out of
  `semantic_manifest.json`.
- `s_layer` blocks in a metric's, dimension's or entity's `config.meta` are validated against
  the S-LAYER vocabulary ([s-layer-semantic-formats](https://github.com/s-layer/semantic-formats));
  `generate` and `check` stop on an invalid block, naming each one.
- Snowflake semantic view members with an `s_layer` block get its synonyms as `WITH SYNONYMS`
  and the tags `S_LAYER_KIND`, `S_LAYER_LABEL` and `S_LAYER_SPEC`, which say what each member
  means. The README shows the `on-run-start` hooks that create the tags.
- Databricks metric view fields with an `s_layer` block get its `synonyms`, and the Unity
  Catalog column tags `S_LAYER_KIND` and `S_LAYER_SPEC`, set by a post-hook after every build.
- DuckDB semantic views (`sem__*.sql`, built by the `duckdb_semantic_views` package with the
  `semantic_views` extension: 0.13 or later on dbt Core, and the 0.11 that dbt Fusion's DuckDB
  loads), one per semantic model. Every column an expression
  reads is qualified with its table, so a column two joined tables share stays unambiguous.
  Members with an `s_layer` block get its synonyms as `WITH SYNONYMS` and, on extension 0.17
  or later, the tags `S_LAYER_KIND`, `S_LAYER_LABEL` and `S_LAYER_SPEC`. Each model checks
  the loaded extension's version when it runs; the `s_layer_duckdb_tags` var overrides that.

### Known limitations

- In a DuckDB view of more than one table, a member whose expression holds a lambda, a
  subquery or a list comprehension is left out, with a warning.
- dbt Core drops a new-spec semantic model on a versioned model, and the metrics declared with
  it. dbt-s-layer warns, naming both, and suggests parsing with Fusion.
- Cumulative and conversion metrics, metric filters, and some percentile kinds are left out of
  semantic and metric views (see the README).

[Unreleased]: https://github.com/s-layer/dbt-s-layer/commits/develop
