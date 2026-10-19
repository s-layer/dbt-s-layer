{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('customers', v=2) }}"
dimensions:
  - name: "cust"
    expr: "source.customer_id"
  - name: "ordered_at"
    expr: "source.ordered_at"
measures:
  - name: "cust_total"
    expr: "SUM(source.amount)"
