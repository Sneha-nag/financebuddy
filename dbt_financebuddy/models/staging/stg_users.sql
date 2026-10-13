with source as (
    select * from {{ source('bronze', 'users') }}
)

select
    user_id,
    display_name,
    city,
    state,
    persona,
    cast(monthly_income as double) as monthly_income,
    cast(created_at as date) as created_at
from source