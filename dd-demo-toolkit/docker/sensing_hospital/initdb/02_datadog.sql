-- Datadog Database Monitoring (DBM) setup for sensing-postgres.
-- Creates the read-only `datadog` user with pg_monitor + the explain_statement
-- helper so the Agent's postgres integration (dbm: true) can collect query
-- metrics, samples, and explain plans. Mirrors the authorization-engine DBM
-- setup. pg_stat_statements is preloaded via the postgres `command` in
-- docker-compose.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE USER datadog WITH PASSWORD 'datadog';
GRANT pg_monitor TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datadog;

-- explain_statement function — enables explain plans without superuser.
CREATE SCHEMA IF NOT EXISTS datadog;
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
