#!/usr/bin/env bash
# Ascension Care Companion — agentic LLM Observability demo (standalone, agentless).
# No Docker, no Agent, no OTel collector — streams straight to Datadog.
#
# Usage:
#   export DD_API_KEY=<key>            # required
#   export DD_SITE=datadoghq.com       # or us3/us5/eu/ap1/ddog-gov
#   ./run.sh                           # continuous (Ctrl-C to stop)
#   ./run.sh --count 25               # emit N traces then exit
#   ./run.sh --interval 2             # seconds between traces
set -euo pipefail
cd "$(dirname "$0")"

if [[ -z "${DD_API_KEY:-}" ]]; then
  echo "DD_API_KEY is not set (required for agentless LLM Observability)." >&2
  echo "  export DD_API_KEY=<key>; export DD_SITE=datadoghq.com" >&2
  exit 2
fi
export DD_SITE="${DD_SITE:-datadoghq.com}"

# Prefer an existing venv/interpreter; otherwise create a throwaway one.
PY="${PYTHON:-python3}"
if ! "$PY" -c "import ddtrace" >/dev/null 2>&1; then
  echo "Installing ddtrace into a local venv (.venv-agentic)…"
  "$PY" -m venv .venv-agentic
  PY=".venv-agentic/bin/python"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
fi

exec "$PY" ascension_care_agent.py "$@"
