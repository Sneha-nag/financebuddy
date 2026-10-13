with source as (
    select * from {{ source('bronze', 'transactions') }}
)

select
    transaction_id,
    user_id,
    cast(transaction_ts as timestamp) as transaction_ts,
    cast(posted_date as date) as posted_date,
    merchant_id,
    merchant_name,
    merchant_descriptor,
    category,
    cast(amount as double) as amount,
    currency,
    txn_type,
    channel,
    city,
    state,
    status,
    cast(is_fraud_label as integer) as is_fraud_label,
    cast(is_duplicate_charge_label as integer) as is_duplicate_charge_label,
    planted_tag,
    abs(cast(amount as double)) as amount_abs,
    case when cast(amount as double) < 0 then 'outflow' else 'inflow' end as cash_direction
from source
where status = 'completed'