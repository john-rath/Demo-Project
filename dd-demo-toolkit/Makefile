# dd-demo-toolkit — common docker compose and dev tasks.
#
# Run `make help` to see what's available.
#
# Secret handling: targets that hit Datadog (up / setup / teardown / ui /
# teardown-all) wrap the underlying command in `op run --env-file=.env --`.
# The .env file holds op:// references; the real secrets stay in 1Password
# and are resolved into short-lived process env vars at command time, per
# the corp secret-handling policy. See README § "Handling secrets".

# Use the same compose project name for every call so containers from
# different working directories don't fragment. Defaults to the dir name,
# which is what compose does anyway — being explicit is safer when this
# Makefile is invoked from a parent directory.
COMPOSE_PROJECT_NAME ?= dd-demo-toolkit
export COMPOSE_PROJECT_NAME

# `--remove-orphans` prunes containers from services that have been
# removed/renamed (kills the "setup-run-*" leftover warnings).
COMPOSE := docker compose
UP_FLAGS := --remove-orphans
# `--rm` makes one-shot `compose run` containers self-delete on exit,
# which is what setup / teardown / teardown-all want.
RUN_FLAGS := --rm --remove-orphans

# `op run --env-file=.env --` resolves op:// references in .env into env
# vars for the child process. Plain values in .env pass through unchanged,
# so this also works during the transition period before all keys are
# migrated to 1Password.
OP_RUN := op run --env-file=.env --

# Read active sub-vertical from .env (empty if not set).
_SUB_VERTICAL := $(shell grep '^DD_DEMO_SUB_VERTICAL=' .env 2>/dev/null \
                   | cut -d= -f2 | tr -d ' \r')

# Read explicit DBM flag from .env (empty if not set).
_DBM := $(shell grep '^DD_DEMO_DBM=' .env 2>/dev/null | cut -d= -f2 | tr -d ' \r')

# Activate the `dbm` profile when DD_DEMO_DBM=true OR DD_DEMO_SUB_VERTICAL=payment-processor.
# ey and bd are resource-only overlays — no extra profile needed.
_DBM_PROFILE := $(if $(or $(filter true,$(_DBM)),$(filter payment-processor,$(_SUB_VERTICAL))),--profile dbm,)

# Read the mock-app (Sensing Hospital) flag from .env (empty if not set).
_MOCK_FLEET := $(shell grep '^DD_DEMO_MOCK_FLEET=' .env 2>/dev/null | cut -d= -f2 | tr -d ' \r')

# Activate the `mock-app` profile when DD_DEMO_MOCK_FLEET=true. Adds the real
# Datadog Agent + instrumented microservices + edge fleet to the default
# lifecycle so they ride along with `make up`.
_MOCK_PROFILE := $(if $(filter true,$(_MOCK_FLEET)),--profile mock-app,)

# Combined profile flags for the default lifecycle targets.
_LIFECYCLE_PROFILES := $(_DBM_PROFILE) $(_MOCK_PROFILE)

.PHONY: help check-op migrate-secrets \
        build rebuild up up-d down logs ps \
        setup teardown teardown-all \
        up-data-obs down-data-obs logs-data-obs logs-dbt dbt-run-now \
        up-mock-app down-mock-app build-mock-app logs-mock-app remediate \
        up-traffic down-traffic logs-traffic \
        up-synthetics down-synthetics synthetics-create synthetics-delete \
        rum-create rum-delete rum-provision \
        logs-payment-processor \
        logs-experiment \
        clean-orphans clean-all \
        ui ui-install test validate

help: ## List available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

## -- Secret handling ---------------------------------------------------------

check-op: ## Verify the 1Password CLI is installed and authenticated
	@command -v op >/dev/null || { \
	    echo ""; \
	    echo "  ✗ The 1Password CLI (\`op\`) is not installed."; \
	    echo "    Install:  brew install --cask 1password-cli"; \
	    echo "    Sign in:  eval \"\$$(op signin)\""; \
	    echo "    Then re-run this make target."; \
	    echo ""; \
	    echo "  Corp policy: secrets live in 1Password, not in .env."; \
	    echo "  See README § 'Handling secrets' for details."; \
	    echo ""; \
	    exit 1; \
	}
	@# Auth probe: `op vault list` works under both the desktop-app
	@# biometric integration AND the `eval "$(op signin)"` session-token
	@# path. `op whoami` is unreliable with biometric integration in op
	@# 2.x — it asks for a default-account context the integration
	@# doesn't populate. `vault list` only succeeds when the session is
	@# actually live, which is what we actually care about.
	@op vault list >/dev/null 2>&1 || { \
	    echo ""; \
	    echo "  ✗ \`op\` is installed but isn't authenticated to a vault."; \
	    echo "    Desktop app users:  open 1Password, unlock it, then re-run."; \
	    echo "    Otherwise:          eval \"\$$(op signin)\" then re-run."; \
	    echo ""; \
	    exit 1; \
	}

migrate-secrets: ## Print step-by-step instructions to migrate a plain .env to 1Password
	@echo ""
	@echo "  Migrating a plain-text .env to 1Password-backed references"
	@echo "  ==========================================================="
	@echo ""
	@echo "  1. Pick a 1Password vault you have write access to (Employee is fine):"
	@echo "       op vault list"
	@echo ""
	@echo "  2. Create a single 1Password item with both keys as fields:"
	@echo "       op item create --category 'API Credential' \\"
	@echo "         --title 'Datadog' --vault Employee \\"
	@echo "         api-key='<paste API key here>' \\"
	@echo "         app-key='<paste APP key here>'"
	@echo ""
	@echo "  3. Update .env to reference the item. Replace the two secret lines"
	@echo "     with these (matching whatever vault/item/field names you used):"
	@echo "       DD_API_KEY=op://Employee/Datadog/api-key"
	@echo "       DD_APP_KEY=op://Employee/Datadog/app-key"
	@echo ""
	@echo "  4. Verify the resolution works:"
	@echo "       op run --env-file=.env -- env | grep DD_API_KEY"
	@echo "     (should print the real key, NOT the op:// literal)"
	@echo ""
	@echo "  5. Revoke the old key in Datadog (https://app.datadoghq.com/organization-settings/api-keys)"
	@echo "     if it was ever copied anywhere outside 1Password — including any backups"
	@echo "     of the old .env, your shell history, or commit drafts."
	@echo ""

## -- Build / lifecycle -------------------------------------------------------

build: ## Build images (uses cache; faster)
	$(COMPOSE) --profile setup --profile teardown --profile teardown-all $(_LIFECYCLE_PROFILES) build

rebuild: ## Build images from scratch (--no-cache; slower, deterministic)
	$(COMPOSE) --profile setup --profile teardown --profile teardown-all $(_LIFECYCLE_PROFILES) build --no-cache

up: check-op ## Start otel-collector + simulator (+ DBM and/or mock-app stacks per .env flags)
	$(OP_RUN) $(COMPOSE) $(_LIFECYCLE_PROFILES) up $(UP_FLAGS)

up-d: check-op ## Same as `up` but detached
	$(OP_RUN) $(COMPOSE) $(_LIFECYCLE_PROFILES) up $(UP_FLAGS) -d

down: ## Stop and remove the long-running services + any orphans
	$(COMPOSE) $(_LIFECYCLE_PROFILES) down --remove-orphans

logs: ## Tail logs from running services
	$(COMPOSE) $(_LIFECYCLE_PROFILES) logs -f

ps: ## Show running containers for this project
	$(COMPOSE) ps

## -- One-shot toolkit actions (profile-gated) --------------------------------

setup: check-op ## Deploy demo resources for $$DD_DEMO_VERTICAL (and overlay if set)
	$(OP_RUN) $(COMPOSE) --profile setup run $(RUN_FLAGS) setup

teardown: check-op ## Tear down resources for $$DD_DEMO_VERTICAL
	$(OP_RUN) $(COMPOSE) --profile teardown run $(RUN_FLAGS) teardown

teardown-all: check-op ## Nuke every dd-demo-toolkit:true resource across all verticals
	$(OP_RUN) $(COMPOSE) --profile teardown-all run $(RUN_FLAGS) teardown-all

## -- Data Streams Monitoring stack (EY AOP demo) -----------------------------

up-data-obs: check-op ## Start the EY DSM stack: kafka + dd-agent + 3 Python workers
	$(OP_RUN) $(COMPOSE) --profile data-obs up $(UP_FLAGS) -d

down-data-obs: ## Stop only the EY DSM stack (leaves otel-collector + simulator running)
	$(COMPOSE) --profile data-obs down --remove-orphans

logs-data-obs: ## Tail logs from the EY DSM workers (producer / pipeline / eval)
	$(COMPOSE) logs -f data-obs-producer data-obs-feature-pipeline data-obs-eval-consumer

logs-dbt: ## Tail logs from the dbt runner (build cycles + datadog-ci upload)
	$(COMPOSE) logs -f data-obs-dbt-runner

## -- Sensing Hospital mock app (real Datadog Agent) --------------------------

up-mock-app: check-op ## Start the Sensing Hospital mock app: dd-agent + microservices + edge fleet
	$(OP_RUN) $(COMPOSE) --profile mock-app up $(UP_FLAGS) -d

down-mock-app: ## Stop only the Sensing Hospital mock app
	$(COMPOSE) --profile mock-app down --remove-orphans

build-mock-app: ## Build the Sensing Hospital mock-app images
	$(COMPOSE) --profile mock-app build

logs-mock-app: ## Tail logs from the mock-app microservices + edge fleet
	$(COMPOSE) logs -f care-portal care-summary-api ai-care-companion rtls-location-service care-experience-platform care-event-router care-event-consumer room-sensing-gateway patient-context-service clinical-alerts-service notification-service edge-device-fleet remediation-controller

up-traffic: check-op ## Start the real-browser RUM traffic generator (Playwright) — run with the mock-app up
	$(OP_RUN) $(COMPOSE) --profile mock-app --profile traffic up $(UP_FLAGS) -d rum-traffic-generator

down-traffic: ## Stop the RUM traffic generator
	$(COMPOSE) --profile traffic down --remove-orphans

logs-traffic: ## Tail the RUM traffic generator
	$(COMPOSE) logs -f rum-traffic-generator

remediate: ## Manually fire the on-prem remediation (clamp rtls poll rate) against the running mock app
	$(COMPOSE) exec remediation-controller python -c "import requests; print(requests.post('http://localhost:8080/remediate', json={'target':'on-prem'}).json())"

## -- Synthetics (drives RUM traffic via a private location) ------------------

up-synthetics: check-op ## Start the Synthetics private-location worker (needs DATADOG_PRIVATE_LOCATION_CONFIG in .env)
	$(OP_RUN) $(COMPOSE) --profile synthetics up $(UP_FLAGS) -d synthetics-private-location

down-synthetics: ## Stop the Synthetics private-location worker
	$(COMPOSE) --profile synthetics down --remove-orphans

synthetics-create: check-op ## Create the Sensing Hospital synthetic tests (browser + API) on the private location
	$(OP_RUN) $(COMPOSE) --profile mock-app run --rm --no-deps \
	  -e DD_API_KEY -e DD_APP_KEY -e DD_SITE -e DD_SYNTHETICS_PRIVATE_LOCATION_ID \
	  care-portal python synthetics/manage_synthetics.py create

synthetics-delete: check-op ## Delete the Sensing Hospital synthetic tests
	$(OP_RUN) $(COMPOSE) --profile mock-app run --rm --no-deps \
	  -e DD_API_KEY -e DD_APP_KEY -e DD_SITE \
	  care-portal python synthetics/manage_synthetics.py delete

## -- RUM application (browser frontend) --------------------------------------

rum-create: check-op ## Create the RUM browser application; prints applicationId + clientToken to set
	$(OP_RUN) $(COMPOSE) --profile mock-app run --rm --no-deps \
	  -e DD_API_KEY -e DD_APP_KEY -e DD_SITE -e RUM_APP_NAME \
	  care-portal python manage_rum.py create

rum-delete: check-op ## Delete the RUM browser application
	$(OP_RUN) $(COMPOSE) --profile mock-app run --rm --no-deps \
	  -e DD_API_KEY -e DD_APP_KEY -e DD_SITE -e RUM_APP_NAME \
	  care-portal python manage_rum.py delete

rum-provision: check-op ## Turnkey RUM (if 'rum' selected): create app, store token in 1Password, write .env op:// refs. Auto-run by `make ui`.
	$(OP_RUN) ./.venv-ui/bin/python -m dd_demo_toolkit_ui.provision_rum

dbt-run-now: check-op ## Trigger an immediate dbt build (no need to wait for the loop interval)
	$(OP_RUN) $(COMPOSE) exec data-obs-dbt-runner bash -c 'cd /dbt_project && dbt build 2>&1 | tail -30'

## -- Database Monitoring stack (DBM demo — any vertical) --------------------

logs-payment-processor: ## Tail logs from the authorization DB worker
	$(COMPOSE) logs -f authorization-db-worker

## -- LLM Obs Experiments (continuous, runs alongside simulator) --------------

logs-experiment: ## Tail logs from the continuous EY LLM Obs experiment runner
	$(COMPOSE) logs -f llm-experiment

## -- Cleanup -----------------------------------------------------------------

clean-orphans: ## Remove exited orphan containers from this project
	@# `compose down --remove-orphans` would also stop running services;
	@# this surgical variant only deletes the exited orphans we don't want.
	@orphans=$$(docker ps -a \
	    --filter "label=com.docker.compose.project=$(COMPOSE_PROJECT_NAME)" \
	    --filter "status=exited" \
	    --format '{{.Names}}'); \
	if [ -z "$$orphans" ]; then \
	    echo "no orphans to remove"; \
	else \
	    echo "removing orphans:"; printf '  %s\n' $$orphans; \
	    echo "$$orphans" | xargs docker rm; \
	fi

clean-all: ## Stop everything for this project, remove containers + orphans
	$(COMPOSE) $(_DBM_PROFILE) down --remove-orphans --volumes

## -- Local UI (Phase 1, runs on the SE's laptop, not in Docker) --------------

ui-install: ## Create .venv-ui (Python 3.13) and install editable + UI extras
	@command -v python3.13 >/dev/null || \
	    { echo "python3.13 not found. brew install python@3.13"; exit 1; }
	python3.13 -m venv .venv-ui
	./.venv-ui/bin/pip install --quiet --upgrade pip
	./.venv-ui/bin/pip install -e '.[dev,ui]'

ui: check-op down build rum-provision ## Clean any running stack, rebuild fresh images, provision RUM, then launch the UI idle (Start from the Simulator tab). http://127.0.0.1:8765
	$(OP_RUN) ./.venv-ui/bin/dd-demo-ui

test: ## Run the unit + static analysis test suite (no secrets needed)
	./.venv/bin/pytest tests/ --no-cov -q -m "not integration"

validate: check-op ## Run live dashboard data checks against Datadog (requires make up first)
	$(OP_RUN) ./.venv/bin/pytest tests/ -m integration -v --no-cov
