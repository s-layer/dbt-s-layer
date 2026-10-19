{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('customers', v=2) }}"
dimensions:
  - name: "country"
    expr: "source.country"
  - name: "customer"
    expr: "source.customer_id"
  - name: "signed_up_at"
    expr: "source.signed_up_at"
measures:
  - name: "customer_count"
    expr: "COUNT(DISTINCT source.customer_id)"
