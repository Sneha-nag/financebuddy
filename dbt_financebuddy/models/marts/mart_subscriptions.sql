-- Rough subscription detector: same merchant, gaps that look ~monthly.
-- Not trying to be Plaid — just good enough for the demo marts / UI.

with candidates as (
    select * from {{ ref('int_recurring_candidates') }}
),

stats as (
    select
        user_id,
        merchant_id,
        max(merchant_name) as merchant_name,
        max(category) as category,
        count(*) as charge_count,
        avg(amount_abs) as avg_amount,
        stddev_pop(amount_abs) as amount_stddev,
        min(amount_abs) as min_amount,
        max(amount_abs) as max_amount,
        avg(gap_days) as avg_gap_days,
        min(posted_date) as first_seen,
        max(posted_date) as last_seen,
        max(case when planted_tag = 'price_creep' then 1 else 0 end) as has_price_creep_tag
    from candidates
    -- Keep ~monthly gaps; null gaps sneak in for the first charge in a series.
    where gap_days between 25 and 40
       or gap_days is null
    group by 1, 2
),

labeled as (
    select
        *,
        case
            when charge_count >= 3 and avg_gap_days between 25 and 40 then true
            when charge_count >= 3 and avg_gap_days is null then false
            else false
        end as is_subscription,
        -- 20%+ swing between min/max on a subscription-looking series => price creep.
        case
            when charge_count >= 3
             and avg_gap_days between 25 and 40
             and category = 'Subscriptions'
             and max_amount > 0 and min_amount > 0
             and (max_amount - min_amount) / nullif(min_amount, 0) >= 0.20
                then true
            else false
        end as price_increased,
        round(avg_amount, 2) as estimated_monthly_cost
    from stats
)

select * from labeled
where charge_count >= 2