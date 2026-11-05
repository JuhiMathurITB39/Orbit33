select wm_yr_wk, min(date) as week_start, cat_id, store_id, sum(units_sold) as units, sum(revenue) as revenue
from {{ ref('stg_sales') }} group by 1, 3, 4
