{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  stores AS {{ ref('fx_dep', 'stores') }}
    PRIMARY KEY (store_id)
)
DIMENSIONS (
  stores.opened_at AS opened_at,
  stores.region AS region,
  stores.store AS store_id
)
