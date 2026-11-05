select supplier_id, count(*) filter (where status <> 'open') as delivered_orders, count(*) filter (where status = 'open') as open_orders,
       avg(actual_lead_days) as avg_lead_days, avg(is_late::int) as late_rate, avg(qty_received / qty_ordered) filter (where status <> 'open') as fill_ratio
from {{ ref('stg_purchase_orders') }}
group by 1
