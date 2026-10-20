{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('orders') }}"
dimensions:
  - name: "customer"
    expr: "source.customer_id"
  - name: "order"
    expr: "source.order_id"
  - name: "ordered_at"
    expr: "source.ordered_at"
measures:
  - name: "revenue"
    display_name: "Revenue"
    expr: "SUM(source.amount)"
