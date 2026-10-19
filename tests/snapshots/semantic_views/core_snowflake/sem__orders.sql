{{ config(materialized='semantic_view', static_analysis='off') }}

TABLES (
  orders AS {{ ref('orders') }}
    PRIMARY KEY (order_id)
    COMMENT = 'One row per order. It''s the ''fact'' table.',
  customers AS {{ ref('customers', v=1) }}
    PRIMARY KEY (customer_id),
  stores AS {{ ref('fx_dep', 'stores') }}
    PRIMARY KEY (store_id)
)
RELATIONSHIPS (
  orders_to_customers AS orders (customer_id) REFERENCES customers (customer_id),
  orders_to_stores AS orders (store_id) REFERENCES stores (store_id)
)
DIMENSIONS (
  orders.customer AS customer_id,
  orders."ORDER" AS order_id,
  orders.ordered_at AS ordered_at,
  orders.status AS status,
  orders.store AS store_id,
  customers.country AS country,
  customers.customer AS customer_id,
  customers.signed_up_at AS signed_up_at,
  stores.opened_at AS opened_at,
  stores.region AS region,
  stores.store AS store_id
)
METRICS (
  orders.latest_revenue NON ADDITIVE BY (orders.ordered_at ASC) AS SUM(amount),
  orders.order_count AS COUNT(DISTINCT order_id) COMMENT = 'Distinct orders.',
  orders.revenue AS SUM(amount),
  orders.revenue_p90 AS PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY amount),
  average_order_value AS orders.revenue / NULLIF(orders.order_count, 0),
  revenue_doubled AS orders.revenue * 2
)
COMMENT = 'One row per order. It''s the ''fact'' table.'
