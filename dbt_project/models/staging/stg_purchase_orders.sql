select po.po_id, p.item_id, s.store_id, su.supplier_id, su.version as supplier_version, od.date as order_date,
       rd.date as received_date, po.qty_ordered, po.qty_received, po.unit_cost, po.status, po.promised_lead_days,
       po.actual_lead_days, po.delay_days, po.is_late
from {{ source('orbit', 'fact_purchase_orders') }} po
join {{ source('orbit', 'dim_product') }} p using (product_key)
join {{ source('orbit', 'dim_store') }} s using (store_key)
join {{ source('orbit', 'dim_supplier') }} su using (supplier_key)
join {{ source('orbit', 'dim_date') }} od on od.date_key = po.order_date_key
left join {{ source('orbit', 'dim_date') }} rd on rd.date_key = po.received_date_key
