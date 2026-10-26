{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  customers AS {{ ref('customers', v=1) }}
    PRIMARY KEY (customer_id)
)
DIMENSIONS (
  customers.country AS customers.country,
  customers.customer AS customers.customer_id,
  customers.signed_up_at AS customers.signed_up_at
)
