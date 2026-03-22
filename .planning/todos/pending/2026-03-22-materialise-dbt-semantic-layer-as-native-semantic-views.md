---
created: 2026-03-22T20:24:51.021Z
title: Materialise dbt semantic layer as native semantic views
area: general
files: []
---

## Problem

The dbt semantic layer defines metrics and dimensions in YAML (potentially `s-layer.yml` or standard dbt semantic layer YAML files), but these definitions aren't materialised as database objects. Users need a way to read these YAML definitions and generate native semantic views that can be consumed directly by query engines.

Target platforms: Snowflake, Databricks, and DuckDB — each with their own DDL syntax for semantic/materialized views.

## Solution

- Parse dbt semantic layer YAML config (detect `s-layer.yml` or standard dbt `semantic_models` / `metrics` YAML)
- Map semantic layer constructs (entities, dimensions, measures, metrics) to platform-native view DDL
- Generate CREATE VIEW / CREATE SEMANTIC VIEW statements per target platform (Snowflake, Databricks, DuckDB)
- Handle dialect differences across the three targets
- TBD: decide on output format (SQL files, dbt macros, or direct execution)
