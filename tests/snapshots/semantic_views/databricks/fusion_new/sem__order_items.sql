{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('order_items') }}"
joins:
  - name: "orders"
    source: "{{ ref('orders') }}"
    "on": "source.order_id = orders.order_id"
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
measures:
  - name: "items_sold"
    expr: "SUM(source.quantity)"
