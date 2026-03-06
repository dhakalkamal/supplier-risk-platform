-- Rolling shipping volume features per supplier.
-- Aggregates AIS port events over 30-day windows.
--
-- Source:      stg_shipping_volume
-- Grain:       one row per supplier_id (as-of today)
-- Note:        only AIS events where supplier_id was resolved are used.
--              Suppliers with no recent shipping activity will not appear here.

with shipping as (
    select * from {{ ref('stg_shipping_volume') }}
    where supplier_id is not null
      and event_date is not null
),

-- 30-day window: port call counts and gross tonnage
window_30d as (
    select
        supplier_id,
        count(*) filter (where event_type = 'arrival')  as port_call_count_30d,
        sum(gross_tonnage)                              as total_tonnage_30d,
        avg(dwell_hours)                                as avg_dwell_time_30d
    from shipping
    where event_date >= CURRENT_DATE - interval '30 days'
    group by supplier_id
),

-- Prior 30-day window (days 31-60) for delta computation
window_30d_prior as (
    select
        supplier_id,
        count(*) filter (where event_type = 'arrival')  as port_call_count_prior_30d,
        sum(gross_tonnage)                              as total_tonnage_prior_30d
    from shipping
    where event_date >= CURRENT_DATE - interval '60 days'
      and event_date < CURRENT_DATE - interval '30 days'
    group by supplier_id
),

-- 7-day window for recent dwell time
window_7d as (
    select
        supplier_id,
        avg(dwell_hours)                                as avg_dwell_time_7d
    from shipping
    where event_date >= CURRENT_DATE - interval '7 days'
      and event_type = 'arrival'
    group by supplier_id
),

-- Baseline: mean and stddev over 90 days for z-score
window_90d_stats as (
    select
        supplier_id,
        avg(daily_tonnage)                              as mean_daily_tonnage,
        stddev(daily_tonnage)                           as stddev_daily_tonnage
    from (
        select
            supplier_id,
            event_date,
            sum(gross_tonnage)                          as daily_tonnage
        from shipping
        where event_date >= CURRENT_DATE - interval '90 days'
        group by supplier_id, event_date
    ) daily
    group by supplier_id
),

combined as (
    select
        w30.supplier_id,

        -- Volume metrics
        w30.port_call_count_30d,

        -- Volume delta vs prior period (positive = increasing, negative = decreasing)
        {{ div0(
            'w30.total_tonnage_30d - coalesce(wp.total_tonnage_prior_30d, 0)',
            'coalesce(wp.total_tonnage_prior_30d, w30.total_tonnage_30d)'
        ) }}                                            as shipping_volume_delta_30d,

        -- Z-score of recent 30d tonnage vs 90d baseline
        {{ div0(
            'w30.total_tonnage_30d - stats.mean_daily_tonnage * 30',
            'stats.stddev_daily_tonnage * sqrt(30)'
        ) }}                                            as shipping_volume_z_score,

        -- Dwell time
        w7.avg_dwell_time_7d,
        w30.avg_dwell_time_30d - w7.avg_dwell_time_7d  as dwell_time_delta,

        -- Anomaly flag: z-score beyond 2 standard deviations signals disruption
        abs(
            {{ div0(
                'w30.total_tonnage_30d - stats.mean_daily_tonnage * 30',
                'stats.stddev_daily_tonnage * sqrt(30)'
            ) }}
        ) > 2.0                                         as shipping_anomaly_flag

    from window_30d w30
    left join window_30d_prior wp   on w30.supplier_id = wp.supplier_id
    left join window_7d w7          on w30.supplier_id = w7.supplier_id
    left join window_90d_stats stats on w30.supplier_id = stats.supplier_id
)

select * from combined
