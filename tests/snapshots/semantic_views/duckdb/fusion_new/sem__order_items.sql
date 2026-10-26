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
  order_items.order AS order_items.order_id,
  order_items.order_item AS order_items.order_item_id,
  order_items.ordered_at AS order_items.ordered_at,
  orders.order__customer AS orders.customer_id,
  orders.order__ordered_at AS orders.ordered_at
)
METRICS (
  order_items.items_sold AS SUM(order_items.quantity)
)
