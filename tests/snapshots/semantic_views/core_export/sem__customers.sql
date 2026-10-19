{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  customers AS {{ ref('customers', v=1) }}
    PRIMARY KEY (customer_id)
)
DIMENSIONS (
  customers.country AS country,
  customers.customer AS customer_id,
  customers.signed_up_at AS signed_up_at
)
