with tx as (
    select * from {{ ref('stg_transactions') }}
)

select
    user_id,
    posted_date,
    category,
    sum(case when cash_direction = 'outflow' then amount_abs else 0 end) as spend_amount,
    sum(case when cash_direction = 'inflow' then amount_abs else 0 end) as income_amount,
    count(*) as txn_count
from tx
group by 1, 2, 3