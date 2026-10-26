{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  stores AS {{ ref('fx_dep', 'stores') }}
    PRIMARY KEY (store_id)
)
DIMENSIONS (
  stores.opened_at AS stores.opened_at,
  stores.region AS stores.region,
  stores.store AS stores.store_id
)
