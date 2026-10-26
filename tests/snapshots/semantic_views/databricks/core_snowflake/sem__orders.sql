{{ config(
    materialized='metric_view',
    static_analysis='off',
    view_update_via_alter=false,
    post_hook=[
        'alter table {{ this }} alter column `order` set tags (\'S_LAYER_KIND\' = \'identifier\', \'S_LAYER_SPEC\' = \'{"display":{"grouping":false}}\')',
        'alter table {{ this }} alter column `ordered_at` set tags (\'S_LAYER_KIND\' = \'date\', \'S_LAYER_SPEC\' = \'{"time":{"grain":"day"}}\')',
        'alter table {{ this }} alter column `status` set tags (\'S_LAYER_KIND\' = \'category\')',
        'alter table {{ this }} alter column `revenue` set tags (\'S_LAYER_KIND\' = \'currency\', \'S_LAYER_SPEC\' = \'{"display":{"decimals":[0,0],"grouping":true,"negative":"minus"},"value":{"currency":"USD"}}\')',
        'alter table {{ this }} alter column `average_order_value` set tags (\'S_LAYER_KIND\' = \'currency\', \'S_LAYER_SPEC\' = \'{"display":{"grouping":true,"negative":"minus"},"value":{"currency":"USD"}}\')'
    ]
) }}

version: 1.1
comment: "One row per order. It's the 'fact' table."
source: "{{ ref('orders') }}"
joins:
  - name: "customers"
    source: "{{ ref('customers', v=1) }}"
    "on": "source.customer_id = customers.customer_id"
  - name: "stores"
    source: "{{ ref('fx_dep', 'stores') }}"
    "on": "source.store_id = stores.store_id"
dimensions:
  - name: "customer"
    expr: "source.customer_id"
  - name: "order"
    expr: "source.order_id"
  - name: "ordered_at"
    expr: "source.ordered_at"
  - name: "status"
    synonyms:
      - "order status"
    expr: "source.status"
  - name: "store"
    expr: "source.store_id"
  - name: "customer__country"
    expr: "customers.country"
  - name: "customer__signed_up_at"
    expr: "customers.signed_up_at"
  - name: "store__opened_at"
    expr: "stores.opened_at"
  - name: "store__region"
    expr: "stores.region"
measures:
  - name: "latest_revenue"
    display_name: "Latest revenue"
    expr: "SUM(source.amount)"
    window:
      - order: "ordered_at"
        range: "current"
        semiadditive: "last"
  - name: "order_count"
    display_name: "Orders"
    comment: "Distinct orders."
    expr: "COUNT(DISTINCT source.order_id)"
  - name: "revenue"
    display_name: "Revenue"
    synonyms:
      - "sales"
      - "turnover"
    expr: "SUM(source.amount)"
  - name: "revenue_p90"
    display_name: "Revenue p90"
    expr: "PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY source.amount)"
  - name: "average_order_value"
    display_name: "AOV"
    expr: "MEASURE(`revenue`) / NULLIF(MEASURE(`order_count`), 0)"
  - name: "revenue_doubled"
    display_name: "Revenue x2"
    expr: "MEASURE(`revenue`) * 2"
