-- Two months, two stores, three customers, and one order that is not paid (3), so revenue,
-- paid_revenue, order counts and averages all differ from one another and between groups.
-- Order 6's customer is not in customers: a join that drops it, rather than grouping it
-- under a NULL country, loses its revenue.
select * from (values
  (1, 1, 1, 'paid', cast('2024-01-03' as date), cast(10.00 as decimal(10, 2))),
  (2, 2, 1, 'paid', cast('2024-01-10' as date), cast(25.00 as decimal(10, 2))),
  (3, 1, 2, 'returned', cast('2024-01-10' as date), cast(7.50 as decimal(10, 2))),
  (4, 3, 2, 'paid', cast('2024-02-02' as date), cast(40.00 as decimal(10, 2))),
  (5, 2, 2, 'paid', cast('2024-02-15' as date), cast(12.50 as decimal(10, 2))),
  (6, 4, 1, 'paid', cast('2024-02-20' as date), cast(5.00 as decimal(10, 2)))
) as t (order_id, customer_id, store_id, status, ordered_at, amount)
