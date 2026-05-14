# dd-demo-toolkit — common docker compose and dev tasks.
#
# Why this exists: `docker compose run setup` (without --rm) leaves an
# exited container behind on every invocation, and `docker compose up`
# then warns about "orphan containers." Every target here wires in the
# right flags so users don't have to remember them.
#
# Run `make help` to see what's available.

# Use the same compose project name for every call so containers from
# different working directories don't fragment. Defaults to the dir name,
# which is what compose does anyway — being explicit is safer when this
# Makefile is invoked from a parent directory.
COMPOSE_PROJECT_NAME ?= dd-demo-toolkit
export COMPOSE_PROJECT_NAME

# Every `docker compose` call gets these flags by default:
#   --remove-orphans  → prune containers from services that have been
#                       removed/renamed (this kills the warning we kept
#                       seeing about setup-run-* leftovers)
COMPOSE := docker compose
UP_FLAGS := --remove-orphans
# `--rm` makes one-shot `compose run` containers self-delete on exit,
# which is what setup / teardown / teardown-all want.
RUN_FLAGS := --rm --remove-orphans

.PHONY: help build rebuild up down logs ps \
        setup teardown teardown-all \
        clean-orphans clean-all \
        ui ui-install test

help: ## List available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

## -- Build / lifecycle -------------------------------------------------------

build: ## Build images (uses cache; faster)
	$(COMPOSE) build

rebuild: ## Build images from scratch (--no-cache; slower, deterministic)
	$(COMPOSE) build --no-cache

up: ## Start otel-collector + simulator in the foreground; orphans auto-pruned
	$(COMPOSE) up $(UP_FLAGS)

up-d: ## Same as `up` but detached
	$(COMPOSE) up $(UP_FLAGS) -d

down: ## Stop and remove the long-running services + any orphans
	$(COMPOSE) down --remove-orphans

logs: ## Tail logs from running services
	$(COMPOSE) logs -f

ps: ## Show running containers for this project
	$(COMPOSE) ps

## -- One-shot toolkit actions (profile-gated) --------------------------------

setup: ## Deploy demo resources for $$DD_DEMO_VERTICAL (and overlay if set)
	$(COMPOSE) --profile setup run $(RUN_FLAGS) setup

teardown: ## Tear down resources for $$DD_DEMO_VERTICAL
	$(COMPOSE) --profile teardown run $(RUN_FLAGS) teardown

teardown-all: ## Nuke every dd-demo-toolkit:true resource across all verticals
	$(COMPOSE) --profile teardown-all run $(RUN_FLAGS) teardown-all

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

ui: ## Launch the local web UI on http://127.0.0.1:8765 (requires `make ui-install` first)
	./.venv-ui/bin/dd-demo-ui

test: ## Run the toolkit + UI test suite
	./.venv-ui/bin/pytest tests/ --no-cov -q
