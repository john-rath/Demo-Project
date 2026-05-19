# dbt-runner image — drives the EY Data Observability demo for dbt Core.
#
# Runs three things in a loop:
#   1. `dbt seed`  — refreshes CSV seeds into Postgres
#   2. `dbt build` — runs models + executes schema tests
#   3. `datadog-ci dbt upload` — sends manifest.json + run_results.json
#      to Datadog Data Observability so the lineage DAG, freshness, and
#      test results populate in the product UI.
#
# Python (for dbt) + Node (for datadog-ci) in the same image. The
# alternative of two separate containers sharing a target/ volume would
# work but adds compose complexity for no real benefit at this scale.

FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates postgresql-client git \
    && rm -rf /var/lib/apt/lists/*

# Node 20 LTS from NodeSource — datadog-ci requires Node >=18.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @datadog/datadog-ci

# dbt-core + dbt-postgres pinned to a known-good 1.9 line.
RUN pip install --no-cache-dir \
        dbt-core==1.9.0 \
        dbt-postgres==1.9.0

# Project files mounted at /dbt_project so the runner script + dbt
# operate from a single canonical location. profiles.yml comes via
# env-var-driven substitution in /root/.dbt/profiles.yml.
COPY dbt_project/ /dbt_project/
RUN mkdir -p /root/.dbt \
    && cp /dbt_project/profiles.yml /root/.dbt/profiles.yml

COPY dbt_runner/run_loop.sh /app/run_loop.sh
RUN chmod +x /app/run_loop.sh

WORKDIR /dbt_project

ENV PYTHONUNBUFFERED=1
CMD ["/app/run_loop.sh"]
