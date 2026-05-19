{{ config(materialized='view') }}

-- Staging layer for market feeds. Computes null_rate per symbol — the
-- synthetic data-quality signal that the model_input_features mart
-- later uses to flag eval-set rows likely to drive LLM regressions.

with source as (

    select * from {{ source('raw', 'raw_market_feeds') }}

),

per_symbol_stats as (

    select
        symbol,
        count(*) as observations,
        count(*) filter (where last_price is null) as null_price_obs,
        count(*) filter (where bid_ask_spread_bps is null) as null_spread_obs,
        max(received_at) as last_received_at
    from source
    group by symbol

),

with_quality as (

    select
        symbol,
        observations,
        round(
            ((null_price_obs + null_spread_obs)::numeric / nullif(observations * 2, 0)) * 100,
            2
        ) as null_rate_pct,
        last_received_at
    from per_symbol_stats

)

select * from with_quality
