{{ config(materialized='metric_view', static_analysis='off') }}

version: 1.1
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
  - name: "revenue"
    display_name: "Revenue"
    expr: "SUM(source.amount)"
