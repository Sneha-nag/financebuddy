-- Flag people paying for two services that do the same job (Spotify + Apple Music, etc).

with subs as (
    select *
    from {{ ref('mart_subscriptions') }}
    where is_subscription
),

-- Hand-mapped groups on purpose — keeps the demo obvious and explainable.
grouped as (
    select
        *,
        case
            when merchant_id in ('m_spotify', 'm_apple_music') then 'music_streaming'
            when merchant_id in ('m_netflix', 'm_hulu', 'm_disney') then 'video_streaming'
            when merchant_id in ('m_icloud', 'm_dropbox') then 'cloud_storage'
            else null
        end as overlap_group
    from subs
),

dupes as (
    select
        user_id,
        overlap_group,
        array_agg(merchant_name) as merchant_names,
        array_agg(merchant_id) as merchant_ids,
        count(*) as service_count,
        round(sum(estimated_monthly_cost), 2) as combined_monthly_cost,
        round(sum(estimated_monthly_cost) - min(estimated_monthly_cost), 2) as potential_monthly_savings
    from grouped
    where overlap_group is not null
    group by 1, 2
    having count(*) >= 2
)

select
    user_id,
    overlap_group,
    merchant_names,
    merchant_ids,
    service_count,
    combined_monthly_cost,
    potential_monthly_savings,
    'Paying for multiple overlapping ' || replace(overlap_group, '_', ' ') || ' services' as reason
from dupes