select
    t.transaction_id,
    t.user_id,
    t.transaction_ts,
    t.posted_date,
    t.merchant_id,
    t.merchant_name,
    t.merchant_descriptor,
    n.descriptor_normalized,
    t.category,
    t.amount,
    t.amount_abs,
    t.cash_direction,
    t.txn_type,
    t.channel,
    t.city,
    t.state,
    t.is_fraud_label,
    t.is_duplicate_charge_label,
    t.planted_tag,
    date_trunc('month', t.posted_date)::date as month_start
from {{ ref('stg_transactions') }} t
left join {{ ref('int_merchant_normalized') }} n
    on t.transaction_id = n.transaction_id