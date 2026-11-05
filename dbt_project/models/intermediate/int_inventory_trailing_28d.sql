-- Trailing-28-day demand and service per product-store: the basis for days-of-cover and fill-rate marts.
select item_id, store_id, cat_id,
       sum(units_demanded) as demand_28d, sum(units_fulfilled) as fulfilled_28d, sum(lost_sales_units) as lost_28d,
       avg(stockout_flag::int) as stockout_day_rate_28d
from {{ ref('stg_inventory') }}
where date > (select max(date) from {{ ref('stg_inventory') }}) - interval '28 days'
group by 1, 2, 3
