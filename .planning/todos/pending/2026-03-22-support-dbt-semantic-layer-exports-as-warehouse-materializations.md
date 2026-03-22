---
created: 2026-03-22T20:25:30.000Z
title: Support dbt semantic layer exports as warehouse materializations
area: general
files: []
---

## Problem

The dbt semantic layer supports an `exports` concept — saved queries that can be materialised to the warehouse as tables. Other platforms have analogous features: Databricks has materializations, Cube.dev has pre-aggregations. Currently there's no tooling to read these export definitions and materialise them within a dbt project.

Users need a way to define exports in the semantic layer config and have them automatically materialised as dbt models/tables in the warehouse, bridging the gap between the semantic layer and physical warehouse objects.

## Solution

- Parse `exports` definitions from dbt semantic layer YAML (saved queries with export config)
- Map to equivalent concepts across platforms:
  - dbt Cloud: native exports (saved queries → tables)
  - Databricks: materializations
  - Cube.dev: pre-aggregations
- Generate dbt models or run statements that materialise exports as warehouse tables
- Handle refresh/incremental strategies per platform
- TBD: decide whether to generate dbt models, use `run-operation`, or create a custom materialisation
