{{ config(
    materialized='metric_view',
    static_analysis='off',
    view_update_via_alter=false,
    post_hook=[
        'alter table {{ this }} alter column `order__ordered_at` set tags (\'S_LAYER_KIND\' = \'date\', \'S_LAYER_SPEC\' = \'{"time":{"grain":"day"}}\')',
        'alter table {{ this }} alter column `order__status` set tags (\'S_LAYER_KIND\' = \'category\')'
    ]
) }}

version: 1.1
source: "{{ ref('order_items') }}"
joins:
  - name: "orders"
    source: "{{ ref('orders') }}"
    "on": "source.order_id = orders.order_id"
    joins:
      - name: "customers"
        source: "{{ ref('customers', v=1) }}"
        "on": "orders.customer_id = customers.customer_id"
      - name: "stores"
        source: "{{ ref('fx_dep', 'stores') }}"
        "on": "orders.store_id = stores.store_id"
dimensions:
  - name: "order"
    expr: "source.order_id"
  - name: "order_item"
    expr: "source.order_item_id"
  - name: "ordered_at"
    expr: "source.ordered_at"
  - name: "order__customer"
    expr: "orders.customer_id"
  - name: "order__ordered_at"
    expr: "orders.ordered_at"
  - name: "order__status"
    synonyms:
      - "order status"
    expr: "orders.status"
  - name: "order__store"
    expr: "orders.store_id"
  - name: "order__customer__country"
    expr: "orders.customers.country"
  - name: "order__customer__signed_up_at"
    expr: "orders.customers.signed_up_at"
  - name: "order__store__opened_at"
    expr: "orders.stores.opened_at"
  - name: "order__store__region"
    expr: "orders.stores.region"
measures:
  - name: "items_sold"
    expr: "SUM(source.quantity)"
