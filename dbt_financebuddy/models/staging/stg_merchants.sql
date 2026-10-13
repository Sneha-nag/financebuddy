with source as (
    select * from {{ source('bronze', 'merchants') }}
)

select
    merchant_id,
    name as merchant_name,
    category,
    cast(typical_amount as double) as typical_amount,
    cast(is_subscription as boolean) as is_subscription_catalog
from source