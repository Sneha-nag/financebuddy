with tx as (
    select * from {{ ref('fct_transactions') }}
),

monthly as (
    select
        user_id,
        month_start,
        category,
        sum(case when cash_direction = 'outflow' then amount_abs else 0 end) as spend,
        sum(case when cash_direction = 'inflow' then amount_abs else 0 end) as income,
        count(*) as txn_count
    from tx
    group by 1, 2, 3
)

select
    user_id,
    month_start,
    category,
    spend,
    income,
    txn_count,
    sum(spend) over (partition by user_id, month_start) as total_month_spend,
    sum(income) over (partition by user_id, month_start) as total_month_income
from monthly