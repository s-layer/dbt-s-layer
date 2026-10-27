{{ config(materialized='semantic_view', static_analysis='off') }}
{%- set s_layer_tags = var('s_layer_duckdb_tags', none) %}
{%- if s_layer_tags is none %}
{%- set s_layer_tags = execute and run_query(
    "select count(*) from duckdb_extensions() where extension_name = 'semantic_views' and loaded and regexp_matches(extension_version, '^v(0[.](1[7-9]|[2-9][0-9])|[1-9][0-9]*)[.]')"
).columns[0].values()[0] > 0 %}
{%- endif %}

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
  orders.customer AS orders.customer_id,
  orders.order AS orders.order_id{% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'identifier', S_LAYER_SPEC = '{"display":{"grouping":false}}'){% endif %},
  orders.order_month AS date_trunc('month', orders.ordered_at),
  orders.ordered_at AS orders.ordered_at{% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'date', S_LAYER_SPEC = '{"time":{"grain":"day"}}'){% endif %},
  orders.status AS orders.status WITH SYNONYMS = ('order status'){% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'category'){% endif %},
  orders.store AS orders.store_id,
  customers.customer__country AS customers.country,
  customers.customer__signed_up_at AS customers.signed_up_at,
  stores.store__opened_at AS stores.opened_at,
  stores.store__region AS stores.region
)
METRICS (
  orders.latest_revenue NON ADDITIVE BY (orders.ordered_at ASC) AS SUM(orders.amount),
  orders.order_count AS COUNT(DISTINCT orders.order_id) COMMENT = 'Distinct orders.',
  orders.paid_revenue AS SUM(CASE WHEN orders.status = 'paid' THEN orders.amount ELSE 0 END),
  orders.revenue AS SUM(orders.amount) WITH SYNONYMS = ('sales', 'turnover'){% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'currency', S_LAYER_LABEL = 'Revenue', S_LAYER_SPEC = '{"display":{"decimals":[0,0],"grouping":true,"negative":"minus"},"value":{"currency":"USD"}}'){% endif %},
  orders.revenue_p90 AS PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY orders.amount),
  average_order_value AS revenue / NULLIF(order_count, 0){% if s_layer_tags %} WITH TAG (S_LAYER_KIND = 'currency', S_LAYER_LABEL = 'AOV', S_LAYER_SPEC = '{"display":{"grouping":true,"negative":"minus"},"value":{"currency":"USD"}}'){% endif %},
  revenue_doubled AS revenue * 2
)
COMMENT = 'One row per order. It''s the ''fact'' table.'
