-- Order 1 has two items and order 3 none, so an item count is not an order count.
select * from (values
  (1, 1, cast('2024-01-03' as date), 2),
  (2, 1, cast('2024-01-03' as date), 1),
  (3, 2, cast('2024-01-10' as date), 4),
  (4, 4, cast('2024-02-02' as date), 3),
  (5, 5, cast('2024-02-15' as date), 1),
  (6, 6, cast('2024-02-20' as date), 2)
) as t (order_item_id, order_id, ordered_at, quantity)
