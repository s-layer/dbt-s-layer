{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  customers AS {{ ref('customers', v=2) }}
    PRIMARY KEY (customer_id)
)
DIMENSIONS (
  customers.cust AS customer_id,
  customers.ordered_at AS ordered_at
)
METRICS (
  customers.cust_total AS SUM(amount)
)
