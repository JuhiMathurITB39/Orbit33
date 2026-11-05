select f.date_key, d.date, d.wm_yr_wk, p.item_id, p.cat_id, s.store_id, f.units_sold, f.sell_price,
       f.units_sold * f.sell_price as revenue
from {{ source('orbit', 'fact_sales') }} f
join {{ source('orbit', 'dim_date') }} d using (date_key)
join {{ source('orbit', 'dim_product') }} p using (product_key)
join {{ source('orbit', 'dim_store') }} s using (store_key)
