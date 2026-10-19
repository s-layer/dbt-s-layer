{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('customers', v=1) }}"
dimensions:
  - name: "country"
    expr: "source.country"
  - name: "customer"
    expr: "source.customer_id"
  - name: "signed_up_at"
    expr: "source.signed_up_at"
