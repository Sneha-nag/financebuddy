select
    user_id,
    display_name,
    city,
    state,
    persona,
    monthly_income,
    created_at
from {{ ref('stg_users') }}