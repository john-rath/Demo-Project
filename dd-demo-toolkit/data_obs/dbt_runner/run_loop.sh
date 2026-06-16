#!/usr/bin/env bash
# EY dbt runner loop. Boots dbt, runs a build cycle on an interval,
# uploads artifacts to Datadog Data Observability after each cycle.
set -uo pipefail

PROJECT_DIR=/dbt_project
INTERVAL_SEC=${DBT_RUN_INTERVAL_SEC:-600}
DBT_SERVICE_NAME=${DBT_SERVICE_NAME:-analytics-dbt-runner}
DD_ENV=${DD_ENV:-demo}

cd "$PROJECT_DIR"

echo "[dbt-runner] waiting for postgres ${DBT_POSTGRES_HOST}:${DBT_POSTGRES_PORT} as ${DBT_POSTGRES_USER}..."
until pg_isready -h "$DBT_POSTGRES_HOST" -p "$DBT_POSTGRES_PORT" -U "$DBT_POSTGRES_USER" >/dev/null 2>&1; do
  sleep 2
done
echo "[dbt-runner] postgres ready"

echo "[dbt-runner] dbt deps (installing dbt_utils)"
dbt deps || { echo "[dbt-runner] dbt deps failed"; exit 1; }

echo "[dbt-runner] initial dbt seed --full-refresh"
dbt seed --full-refresh || echo "[dbt-runner] initial seed had issues; continuing into loop"

report_artifacts() {
  # The dbt project produces canonical artifacts under target/. The
  # *upload* path to Datadog Data Observability is currently handled
  # OUT-OF-BAND (datadog-ci dropped the dbt plugin in v5; the Agent's
  # dbt integration is the supported path). We log the artifact mtimes
  # + sizes here so the SE can rsync target/ out and feed it into
  # whatever upload mechanism their Datadog account is configured for.
  if [ ! -f target/manifest.json ] || [ ! -f target/run_results.json ]; then
    echo "[dbt-runner] artifacts missing (manifest.json or run_results.json)"
    return 1
  fi
  echo "[dbt-runner] artifacts ready (upload via Datadog Agent dbt integration or manual):"
  ls -lh target/manifest.json target/run_results.json | awk '{print "  ", $0}'
}

while true; do
  echo ""
  echo "[dbt-runner] ============ run starting $(date -u +%FT%TZ) ============"

  # Refresh seeds so a small amount of demo data churn shows up in the
  # Data Observability freshness view between runs.
  dbt seed 2>&1 | tail -5 || true

  echo "[dbt-runner] dbt build (models + tests)"
  dbt build 2>&1 | tail -30
  BUILD_RC=${PIPESTATUS[0]}
  echo "[dbt-runner] dbt build rc=$BUILD_RC"

  report_artifacts || true

  echo "[dbt-runner] sleeping ${INTERVAL_SEC}s before next run"
  sleep "$INTERVAL_SEC"
done
