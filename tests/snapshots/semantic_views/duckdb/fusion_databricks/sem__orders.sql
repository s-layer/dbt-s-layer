{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
    COMMENT = 'One row per order. It''s the ''fact'' table.',
  customers AS {{ ref('customers', v=2) }}
    PRIMARY KEY (customer_id),
  stores AS {{ ref('fx_dep', 'stores') }}
    PRIMARY KEY (store_id)
)
RELATIONSHIPS (
  orders_to_customers AS orders (customer_id) REFERENCES customers (customer_id),
  orders_to_stores AS orders (store_id) REFERENCES stores (store_id)
)
DIMENSIONS (
  orders.customer AS orders.customer_id,
  orders.order AS orders.order_id,
  orders.ordered_at AS orders.ordered_at,
  orders.status AS orders.status WITH SYNONYMS = ('order status'),
  orders.store AS orders.store_id,
  customers.customer__country AS customers.country,
  customers.customer__signed_up_at AS customers.signed_up_at,
  stores.store__opened_at AS stores.opened_at,
  stores.store__region AS stores.region
)
METRICS (
  orders.latest_revenue NON ADDITIVE BY (orders.ordered_at ASC) AS SUM(orders.amount),
  orders.order_count AS COUNT(DISTINCT orders.order_id) COMMENT = 'Distinct orders.',
  orders.revenue AS SUM(orders.amount) WITH SYNONYMS = ('sales', 'turnover'),
  orders.revenue_p90 AS PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY orders.amount),
  average_order_value AS revenue / NULLIF(order_count, 0),
  revenue_doubled AS revenue * 2
)
COMMENT = 'One row per order. It''s the ''fact'' table.'
