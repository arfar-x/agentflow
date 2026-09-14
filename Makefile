# Thin wrapper around the scripts/ and docker compose commands already
# documented in README.md and docs/OPERATIONS.md -- no logic lives here
# that isn't already in one of those, this just saves typing.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
SERVICE ?= open-webui

.PHONY: help bootstrap up down restart ps logs build secrets backup restore test \
	user-create user-list user-ban user-unban user-delete user-reset-password

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Start the whole stack -- build, wait healthy, create admin if needed. Safe to rerun.
	scripts/bootstrap.sh

bootstrap: up ## Alias for `up` (same idempotent first-run/re-run sequence)

down: ## Stop the stack (volumes are kept)
	docker compose down

restart: ## Restart one service, e.g. `make restart SERVICE=open-webui`
	docker compose restart $(SERVICE)

ps: ## Show service status/health
	docker compose ps

logs: ## Tail logs for one service, e.g. `make logs SERVICE=mcp-agent-skills`
	docker compose logs -f $(SERVICE)

build: ## Rebuild one service's image, e.g. `make build SERVICE=mcp-agent-skills`
	docker compose up -d --build $(SERVICE)

secrets: ## Generate the secrets .env needs (refuses to run on a live deployment)
	scripts/generate-secrets.sh

backup: ## Snapshot every named volume + .env into backups/<timestamp>/
	scripts/backup.sh

restore: ## Restore from a backup dir, e.g. `make restore DIR=backups/20260101-000000`
	scripts/restore.sh $(DIR)

test: ## Run mcp-server's pytest suite inside the built mcp-agent-skills image
	docker compose run --rm --user root --entrypoint sh mcp-agent-skills -c \
		"pip install --no-cache-dir pytest -q >/dev/null && cd mcp-server && python3 -m pytest -q"

# ---- User management -------------------------------------------------------
# Thin wrappers over Open WebUI's own admin REST API (scripts/openwebui-
# admin.sh) -- nothing custom here beyond what that script does, just saved
# typing. For ROLE/PERMISSION management beyond ban/unban (custom roles,
# delegated grants, groups) use the Admin Panel instead, built into Open
# WebUI itself: http://localhost:$${PORT:-3080}/admin -- log in with an
# existing admin account.

user-create: ## Create a user, e.g. `make user-create EMAIL=a@b.com NAME="A B" [PASSWORD=...]`
	@test -n "$(EMAIL)" && test -n "$(NAME)" || \
		{ echo "Usage: make user-create EMAIL=... NAME=... [PASSWORD=...]" >&2; exit 1; }
	scripts/openwebui-admin.sh create "$(EMAIL)" "$(NAME)" "$(PASSWORD)"

user-list: ## List every user account
	scripts/openwebui-admin.sh list

user-ban: ## Block a user's access, e.g. `make user-ban EMAIL=a@b.com` -- NOT time-limited, see docs/CONFIGURATION.md
	@test -n "$(EMAIL)" || { echo "Usage: make user-ban EMAIL=..." >&2; exit 1; }
	scripts/openwebui-admin.sh ban "$(EMAIL)"

user-unban: ## Reverse `make user-ban`, e.g. `make user-unban EMAIL=a@b.com`
	@test -n "$(EMAIL)" || { echo "Usage: make user-unban EMAIL=..." >&2; exit 1; }
	scripts/openwebui-admin.sh unban "$(EMAIL)"

user-delete: ## Delete a user and ALL their data -- interactive, asks you to confirm (irreversible)
	@test -n "$(EMAIL)" || { echo "Usage: make user-delete EMAIL=..." >&2; exit 1; }
	scripts/openwebui-admin.sh delete "$(EMAIL)"

user-reset-password: ## Reset a user's password, e.g. `make user-reset-password EMAIL=a@b.com [PASSWORD=...]`
	@test -n "$(EMAIL)" || { echo "Usage: make user-reset-password EMAIL=..." >&2; exit 1; }
	scripts/openwebui-admin.sh reset-password "$(EMAIL)" "$(PASSWORD)"
