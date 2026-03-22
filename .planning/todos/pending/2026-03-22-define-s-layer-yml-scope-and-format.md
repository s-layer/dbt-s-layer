---
created: 2026-03-22T20:26:15.000Z
title: Define s-layer.yml scope and format
area: general
files: []
---

## Problem

The project needs a clear configuration format (`s-layer.yml`) that defines what the semantic layer tool reads as input. Without a well-defined schema, downstream features (native semantic views, exports/materializations) have no stable contract to build against.

Key questions to answer:
- What is the relationship between `s-layer.yml` and standard dbt semantic layer YAML (`semantic_models`, `metrics`, `saved_queries`)?
- Is `s-layer.yml` a superset, a subset, or a translation layer?
- What sections/keys does it support (targets, exports, semantic views, platform config)?
- How does it reference or extend existing dbt project files?

## Solution

- Define the YAML schema for `s-layer.yml` — top-level keys, nesting, and types
- Document the scope: what it controls vs what it delegates to dbt's own config
- Include platform-specific sections (Snowflake, Databricks, DuckDB config)
- Provide example files showing common use cases
- TBD: decide if it's a standalone file or integrates into `dbt_project.yml`
