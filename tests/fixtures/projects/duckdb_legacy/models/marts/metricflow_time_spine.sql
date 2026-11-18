-- Every day the orders fall on, for the cumulative metric.
select cast(range as date) as date_day
from range(cast('2024-01-01' as date), cast('2024-03-01' as date), interval 1 day)
