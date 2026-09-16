# Thin wrapper around the scripts/ and docker compose commands already
# documented in README.md and docs/OPERATIONS.md -- no logic lives here
# that isn't already in one of those, this just saves typing.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
SERVICE ?= api

.PHONY: help bootstrap up down restart ps logs build secrets backup restore test

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Start the whole stack -- build, wait healthy, create admin + declarative config if needed. Safe to rerun.
	scripts/bootstrap.sh

bootstrap: up ## Alias for `up` (same idempotent first-run/re-run sequence)

down: ## Stop the stack (volumes/ bind mounts are kept)
	docker compose down

restart: ## Restart one service, e.g. `make restart SERVICE=api`
	docker compose restart $(SERVICE)

ps: ## Show service status/health
	docker compose ps

logs: ## Tail logs for one service, e.g. `make logs SERVICE=mcp-agent-skills`
	docker compose logs -f $(SERVICE)

build: ## Rebuild one service's image, e.g. `make build SERVICE=mcp-agent-skills`
	docker compose up -d --build $(SERVICE)

secrets: ## Generate the secrets .env needs (refuses to run on a live deployment)
	scripts/generate-secrets.sh

backup: ## Snapshot volumes/ + .env into backups/<timestamp>/
	scripts/backup.sh

restore: ## Restore from a backup dir, e.g. `make restore DIR=backups/20260101-000000`
	scripts/restore.sh $(DIR)

test: ## Run mcp-server's pytest suite inside the built mcp-agent-skills image
	docker compose run --rm --user root --entrypoint sh mcp-agent-skills -c \
		"pip install --no-cache-dir pytest -q >/dev/null && cd mcp-server && python3 -m pytest -q"

# ---- Managing workspace members ---------------------------------------------
# Dify has no CLI equivalent to LibreChat's/Open WebUI's user-management
# scripts -- workspace members are invited from inside the app itself:
# Studio -> Settings -> Members -> Invite Members (email + role: admin/
# editor/normal/dataset operator). The account created by `make up` (from
# DIFY_ADMIN_EMAIL in .env) is the first member and always an owner; invite
# everyone else that same way. See docs/CONFIGURATION.md "Auth".
