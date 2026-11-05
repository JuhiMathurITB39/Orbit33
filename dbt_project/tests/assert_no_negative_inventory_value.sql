select * from {{ ref('mart_inventory_health') }} where inventory_value < 0
