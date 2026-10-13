select
    merchant_id,
    merchant_name,
    category,
    typical_amount,
    is_subscription_catalog
from {{ ref('stg_merchants') }}