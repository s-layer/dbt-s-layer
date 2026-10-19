{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
)
DIMENSIONS (
  orders.customer AS customer_id,
  orders."ORDER" AS order_id,
  orders.ordered_at AS ordered_at
)
METRICS (
  orders.revenue AS SUM(amount)
)
