select f.date_key, d.date, p.item_id, p.cat_id, p.unit_cost, s.store_id, f.on_hand_end, f.on_order_end, f.units_demanded,
       f.units_fulfilled, f.lost_sales_units, f.stockout_flag
from {{ source('orbit', 'fact_inventory') }} f
join {{ source('orbit', 'dim_date') }} d using (date_key)
join {{ source('orbit', 'dim_product') }} p using (product_key)
join {{ source('orbit', 'dim_store') }} s using (store_key)
