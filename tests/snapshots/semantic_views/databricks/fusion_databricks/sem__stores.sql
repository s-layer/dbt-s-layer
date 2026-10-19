{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
source: "{{ ref('fx_dep', 'stores') }}"
dimensions:
  - name: "opened_at"
    expr: "source.opened_at"
  - name: "region"
    expr: "source.region"
  - name: "store"
    expr: "source.store_id"
measures:
  - name: "store_count"
    expr: "SUM(CASE WHEN store_id IS NOT NULL THEN 1 ELSE 0 END)"
