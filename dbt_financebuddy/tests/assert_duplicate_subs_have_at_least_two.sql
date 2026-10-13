-- Duplicate subscription groups must contain 2+ services
select *
from {{ ref('mart_duplicate_subscriptions') }}
where service_count < 2