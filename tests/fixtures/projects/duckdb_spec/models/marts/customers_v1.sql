-- Two countries, gb with two customers, so a metric grouped by country sums across customers.
select * from (values
  (1, 'gb', cast('2024-01-01' as date)),
  (2, 'fr', cast('2024-01-05' as date)),
  (3, 'gb', cast('2024-01-20' as date))
) as t (customer_id, country, signed_up_at)
