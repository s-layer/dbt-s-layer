{{ config(materialized='semantic_view', static_analysis='off') }}
{%- set s_layer_tags = var('s_layer_duckdb_tags', true) %}

TABLES (
  order_items AS {{ ref('order_items') }}
    PRIMARY KEY (order_item_id),
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
    COMMENT = 'One row per order. It''s the ''fact'' table.',
  customers AS {{ ref('customers', v=2) }}
    PRIMARY KEY (customer_id),
  stores AS {{ ref('fx_dep', 'stores') }}
    PRIMARY KEY (store_id)
)
RELATIONSHIPS (
  order_items_to_orders AS order_items (order_id) REFERENCES orders (order_id),
  orders_to_customers AS orders (customer_id) REFERENCES customers (customer_id),
  orders_to_stores AS orders (store_id) REFERENCES stores (store_id)
)
DIMENSIONS (
  order_items.order AS order_items.order_id,
  order_items.order_item AS order_items.order_item_id,
  order_items.ordered_at AS order_items.ordered_at,
  orders.order__customer AS orders.customer_id,
  orders.order__order_month AS date_trunc('month', orders.ordered_at),
  orders.order__ordered_at AS orders.ordered_at{% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'date', S_LAYER_SPEC = '{"time":{"grain":"day"}}'){% endif %},
  orders.order__status AS orders.status WITH SYNONYMS = ('order status'){% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'category'){% endif %},
  orders.order__store AS orders.store_id,
  customers.order__customer__country AS customers.country,
  customers.order__customer__signed_up_at AS customers.signed_up_at,
  stores.order__store__opened_at AS stores.opened_at,
  stores.order__store__region AS stores.region
)
METRICS (
  order_items.items_sold AS SUM(order_items.quantity)
)
