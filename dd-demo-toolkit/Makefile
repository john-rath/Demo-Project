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

.PHONY: help check-op migrate-secrets \
        build rebuild up up-d down logs ps \
        setup teardown teardown-all \
        clean-orphans clean-all \
        ui ui-install test

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
	@op whoami >/dev/null 2>&1 || { \
	    echo ""; \
	    echo "  ✗ \`op\` is installed but you aren't signed in."; \
	    echo "    Sign in:  eval \"\$$(op signin)\""; \
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
	$(COMPOSE) build

rebuild: ## Build images from scratch (--no-cache; slower, deterministic)
	$(COMPOSE) build --no-cache

up: check-op ## Start otel-collector + simulator (resolves secrets via op run)
	$(OP_RUN) $(COMPOSE) up $(UP_FLAGS)

up-d: check-op ## Same as `up` but detached
	$(OP_RUN) $(COMPOSE) up $(UP_FLAGS) -d

down: ## Stop and remove the long-running services + any orphans
	$(COMPOSE) down --remove-orphans

logs: ## Tail logs from running services
	$(COMPOSE) logs -f

ps: ## Show running containers for this project
	$(COMPOSE) ps

## -- One-shot toolkit actions (profile-gated) --------------------------------

setup: check-op ## Deploy demo resources for $$DD_DEMO_VERTICAL (and overlay if set)
	$(OP_RUN) $(COMPOSE) --profile setup run $(RUN_FLAGS) setup

teardown: check-op ## Tear down resources for $$DD_DEMO_VERTICAL
	$(OP_RUN) $(COMPOSE) --profile teardown run $(RUN_FLAGS) teardown

teardown-all: check-op ## Nuke every dd-demo-toolkit:true resource across all verticals
	$(OP_RUN) $(COMPOSE) --profile teardown-all run $(RUN_FLAGS) teardown-all

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
	$(COMPOSE) down --remove-orphans --volumes

## -- Local UI (Phase 1, runs on the SE's laptop, not in Docker) --------------

ui-install: ## Create .venv-ui (Python 3.13) and install editable + UI extras
	@command -v python3.13 >/dev/null || \
	    { echo "python3.13 not found. brew install python@3.13"; exit 1; }
	python3.13 -m venv .venv-ui
	./.venv-ui/bin/pip install --quiet --upgrade pip
	./.venv-ui/bin/pip install -e '.[dev,ui]'

ui: check-op ## Launch the local web UI on http://127.0.0.1:8765
	$(OP_RUN) ./.venv-ui/bin/dd-demo-ui

test: ## Run the toolkit + UI test suite (no secrets needed — tests are mocked)
	./.venv-ui/bin/pytest tests/ --no-cov -q
