{{ config(materialized='view') }}

-- Staging layer for counterparty exposures. Computes the derived
-- concentration_pct and covenant_breach flag that the marts use, and
-- strips obviously bad rows so downstream tests aren't drowned in noise.

with source as (

    select * from {{ source('raw', 'raw_counterparty_exposure') }}

),

cleaned as (

    select
        counterparty,
        exposure_usd,
        vol_30d,
        var_99_usd,
        received_at,
        round(
            (exposure_usd / 5000000000.0) * 100.0::numeric, 2
        ) as concentration_pct,
        (exposure_usd / 5000000000.0) * 100.0 > {{ var('high_concentration_threshold_pct') }}
            as covenant_breach
    from source
    where exposure_usd is not null
      and exposure_usd > 0

)

select * from cleaned
