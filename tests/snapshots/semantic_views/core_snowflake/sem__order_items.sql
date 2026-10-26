{{ config(materialized='semantic_view', static_analysis='off') }}
{%- set s_layer_tags = var('s_layer_tag_schema', target.database ~ '.' ~ target.schema) %}

TABLES (
  order_items AS {{ ref('order_items') }}
    PRIMARY KEY (order_item_id),
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
    COMMENT = 'One row per order. It''s the ''fact'' table.',
  customers AS {{ ref('customers', v=1) }}
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
  order_items."ORDER" AS order_id,
  order_items.order_item AS order_item_id,
  order_items.ordered_at AS ordered_at,
  orders.customer AS customer_id,
  orders."ORDER" AS order_id WITH TAG ({{ s_layer_tags }}.S_LAYER_KIND = 'identifier', {{ s_layer_tags }}.S_LAYER_SPEC = '{"display":{"grouping":false}}'),
  orders.ordered_at AS ordered_at WITH TAG ({{ s_layer_tags }}.S_LAYER_KIND = 'date', {{ s_layer_tags }}.S_LAYER_SPEC = '{"time":{"grain":"day"}}'),
  orders.status AS status WITH SYNONYMS = ('order status') WITH TAG ({{ s_layer_tags }}.S_LAYER_KIND = 'category'),
  orders.store AS store_id,
  customers.country AS country,
  customers.customer AS customer_id,
  customers.signed_up_at AS signed_up_at,
  stores.opened_at AS opened_at,
  stores.region AS region,
  stores.store AS store_id
)
METRICS (
  order_items.items_sold AS SUM(quantity)
)
