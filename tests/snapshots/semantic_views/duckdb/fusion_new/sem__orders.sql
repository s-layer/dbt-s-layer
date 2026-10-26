{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
)
DIMENSIONS (
  orders.customer AS orders.customer_id,
  orders.order AS orders.order_id,
  orders.ordered_at AS orders.ordered_at
)
METRICS (
  orders.revenue AS SUM(orders.amount)
)
