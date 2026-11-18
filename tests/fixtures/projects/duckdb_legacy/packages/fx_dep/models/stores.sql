select * from (values
  (1, 'north', cast('2024-01-01' as date)),
  (2, 'south', cast('2024-01-01' as date))
) as t (store_id, region, opened_at)
