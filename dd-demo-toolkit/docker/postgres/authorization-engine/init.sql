-- Authorization Engine database schema
-- Loaded by postgres:16-alpine on first start via /docker-entrypoint-initdb.d/

-- Required for DBM query fingerprinting (samples + explain plans)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Core tables ------------------------------------------------------------

CREATE TABLE auth_transactions (
    id               BIGSERIAL PRIMARY KEY,
    card_token       VARCHAR(64)   NOT NULL,
    merchant_id      VARCHAR(32)   NOT NULL,
    amount_cents     INTEGER       NOT NULL,
    region           VARCHAR(32)   NOT NULL,
    status           VARCHAR(16)   NOT NULL,  -- APPROVED / DECLINED / TIMEOUT
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_auth_tx_card_token   ON auth_transactions(card_token);
CREATE INDEX idx_auth_tx_created_at   ON auth_transactions(created_at DESC);
-- Intentionally NO index on merchant_id — full-scan queries against this
-- column are used by the db-worker during cascade to generate slow-query signal.

CREATE TABLE fraud_scores (
    id               BIGSERIAL PRIMARY KEY,
    card_token       VARCHAR(64)   NOT NULL,
    score            NUMERIC(5,2)  NOT NULL,
    risk_level       VARCHAR(16)   NOT NULL,  -- LOW / MEDIUM / HIGH / CRITICAL
    evaluated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_fraud_card_token ON fraud_scores(card_token);

CREATE TABLE card_limits (
    card_token            VARCHAR(64)  PRIMARY KEY,
    daily_limit_cents     INTEGER      NOT NULL DEFAULT 500000,
    current_day_total_cents INTEGER    NOT NULL DEFAULT 0,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed a small working set of card tokens so queries return rows -----------

INSERT INTO card_limits (card_token, daily_limit_cents, current_day_total_cents)
SELECT
    'card_' || lpad(i::text, 6, '0'),
    500000,
    (random() * 100000)::int
FROM generate_series(1, 500) AS g(i);

-- Datadog user for DBM -------------------------------------------------------
-- Needs pg_monitor for pg_stat_statements + activity views.
-- The datadog schema + explain_statement function enables explain plans
-- without granting the agent superuser access.

CREATE USER datadog WITH PASSWORD 'datadog';
GRANT pg_monitor TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datadog;

CREATE SCHEMA datadog;
GRANT USAGE ON SCHEMA datadog TO datadog;
GRANT CREATE ON SCHEMA datadog TO datadog;

CREATE OR REPLACE FUNCTION datadog.explain_statement(
   l_query TEXT,
   OUT explain JSON
)
RETURNS SETOF JSON AS
$$
DECLARE
  curs REFCURSOR;
  plan JSON;
BEGIN
  OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
  FETCH curs INTO plan;
  CLOSE curs;
  RETURN QUERY SELECT plan;
END;
$$
LANGUAGE 'plpgsql'
RETURNS NULL ON NULL INPUT
SECURITY DEFINER;
