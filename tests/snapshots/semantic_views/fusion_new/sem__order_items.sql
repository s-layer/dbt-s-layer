{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  order_items AS {{ ref('order_items') }}
    PRIMARY KEY (order_item_id),
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
)
RELATIONSHIPS (
  order_items_to_orders AS order_items (order_id) REFERENCES orders (order_id)
)
DIMENSIONS (
  order_items."ORDER" AS order_id,
  order_items.order_item AS order_item_id,
  order_items.ordered_at AS ordered_at,
  orders.customer AS customer_id,
  orders."ORDER" AS order_id,
  orders.ordered_at AS ordered_at
)
METRICS (
  order_items.items_sold AS SUM(quantity)
)
