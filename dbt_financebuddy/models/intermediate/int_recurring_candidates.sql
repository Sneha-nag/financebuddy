-- Charge series per user+merchant plus gap_days between them.
-- mart_subscriptions rolls this up into "looks monthly."

with tx as (
    select *
    from {{ ref('int_merchant_normalized') }}
    where cash_direction = 'outflow'
      and category not in ('Income', 'Transfers')
),

ordered as (
    select
        *,
        lag(posted_date) over (
            partition by user_id, merchant_id
            order by posted_date, transaction_ts
        ) as prev_posted_date,
        lag(amount_abs) over (
            partition by user_id, merchant_id
            order by posted_date, transaction_ts
        ) as prev_amount_abs
    from tx
),

gaps as (
    select
        *,
        date_diff('day', prev_posted_date, posted_date) as gap_days,
        case
            when prev_amount_abs is null then null
            when prev_amount_abs = 0 then null
            else abs(amount_abs - prev_amount_abs) / prev_amount_abs
        end as amount_pct_change
    from ordered
)

select * from gaps