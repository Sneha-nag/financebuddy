with tx as (
    select * from {{ ref('stg_transactions') }}
),

normalized as (
    select
        transaction_id,
        user_id,
        transaction_ts,
        posted_date,
        merchant_id,
        merchant_name,
        merchant_descriptor,
        -- Normalize messy bank descriptors for recurring detection
        upper(
            regexp_replace(
                regexp_replace(merchant_descriptor, '[*][A-Z0-9]+', ''),
                '[^A-Z0-9 ]',
                ' '
            )
        ) as descriptor_normalized,
        category,
        amount,
        amount_abs,
        cash_direction,
        txn_type,
        channel,
        city,
        state,
        is_fraud_label,
        is_duplicate_charge_label,
        planted_tag
    from tx
)

select * from normalized