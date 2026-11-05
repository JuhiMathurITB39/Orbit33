with latest as (
  select * from {{ ref('stg_inventory') }} where date = (select max(date) from {{ ref('stg_inventory') }})
)
select l.item_id, l.store_id, l.cat_id, l.on_hand_end as on_hand, l.on_order_end as on_order,
       l.on_hand_end * l.unit_cost as inventory_value,
       t.demand_28d / 28.0 as avg_daily_demand_28d,
       case when t.demand_28d > 0 then l.on_hand_end / (t.demand_28d / 28.0) end as days_of_cover,
       t.fulfilled_28d / nullif(t.demand_28d, 0) as fill_rate_28d, t.stockout_day_rate_28d
from latest l join {{ ref('int_inventory_trailing_28d') }} t using (item_id, store_id, cat_id)
