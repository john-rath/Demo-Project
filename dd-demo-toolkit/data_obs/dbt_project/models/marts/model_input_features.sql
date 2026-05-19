{{ config(materialized='table') }}

-- The eval-set table the EY LLM workload reads from. One row per
-- (counterparty, symbol) pair carrying the joined risk + market signal,
-- plus a data_quality_ok flag the LLM agent uses to short-circuit
-- evals on degraded inputs.
--
-- Datadog Data Observability shows this model as the terminal node in
-- the lineage DAG. When the freshness test on raw_market_feeds fails
-- or the null_rate test below fails, this model goes red — that is
-- the surface Scott Llewelyn referenced as "data hand in glove".

select
    cps.counterparty,
    mf.symbol,
    cps.max_exposure_usd,
    cps.max_concentration_pct,
    cps.any_covenant_breach,
    mf.null_rate_pct,
    (mf.null_rate_pct < {{ var('high_null_rate_threshold_pct') }})
        as data_quality_ok,
    greatest(cps.last_observed_at, mf.last_received_at) as last_input_at
from {{ ref('counterparty_risk_summary') }} cps
cross join {{ ref('stg_market_feeds') }} mf
