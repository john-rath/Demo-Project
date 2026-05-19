{{ config(materialized='table') }}

-- One row per counterparty with the credit metrics the EY Risk Eval
-- Agent reads when drafting credit memos. This is the artifact the
-- LLM eval workload reads from — Datadog Data Observability shows it
-- as a node in the lineage DAG downstream of stg_counterparty_exposures.

select
    counterparty,
    max(exposure_usd) as max_exposure_usd,
    avg(exposure_usd) as avg_exposure_usd,
    max(concentration_pct) as max_concentration_pct,
    bool_or(covenant_breach) as any_covenant_breach,
    count(*) as observation_count,
    max(received_at) as last_observed_at
from {{ ref('stg_counterparty_exposures') }}
group by counterparty
