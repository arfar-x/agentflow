# Thin wrapper around the scripts/ and docker compose commands already
# documented in README.md and docs/OPERATIONS.md -- no logic lives here
# that isn't already in one of those, this just saves typing.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
SERVICE ?= api

.PHONY: help bootstrap up down restart ps logs build secrets backup restore test \
	kb-init kb-test user-create user-list user-ban user-delete user-invite user-reset-password \
	agent-export agent-import

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Start the whole stack -- build, wait healthy, create admin if needed. Safe to rerun.
	scripts/bootstrap.sh

bootstrap: up ## Alias for `up` (same idempotent first-run/re-run sequence)

down: ## Stop the stack (volumes are kept)
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

backup: ## Snapshot every named volume + .env into backups/<timestamp>/
	scripts/backup.sh

restore: ## Restore from a backup dir, e.g. `make restore DIR=backups/20260101-000000`
	scripts/restore.sh $(DIR)

kb-init: ## Create/upgrade the knowledge base schema + its read-only role (idempotent)
	scripts/kb-init.sh

kb-test: ## Run kb's own suite, including the Postgres-backed tests, against a throwaway database
	scripts/kb-test.sh

test: ## Run mcp-server's pytest suite inside the built mcp-agent-skills image
	docker compose run --rm --user root --entrypoint sh mcp-agent-skills -c \
		"pip install --no-cache-dir pytest -q >/dev/null && cd mcp-server && python3 -m pytest -q"

# ---- User management -------------------------------------------------------
# Thin wrappers over LibreChat's own officially-shipped config/*.js scripts
# (the same tool bootstrap.sh uses for the admin account) -- nothing custom
# here, just saved typing. For ROLE/PERMISSION management (promote to ADMIN,
# grant capabilities, create groups) use the Admin Panel instead:
# http://localhost:${ADMIN_PANEL_PORT:-3000} -- it edits existing accounts,
# it doesn't create them.

user-create: ## Create a user, e.g. `make user-create EMAIL=a@b.com NAME="A B" USERNAME=ab [PASSWORD=...]`
	@test -n "$(EMAIL)" && test -n "$(NAME)" && test -n "$(USERNAME)" || \
		{ echo "Usage: make user-create EMAIL=... NAME=... USERNAME=... [PASSWORD=...]" >&2; exit 1; }
	@pw="$(PASSWORD)"; \
	if [ -z "$$pw" ]; then pw="$$(openssl rand -hex 12)"; fi; \
	echo "y" | docker compose exec -T api node config/create-user.js "$(EMAIL)" "$(NAME)" "$(USERNAME)" "$$pw" && \
	echo "Login: $(EMAIL) / $$pw"

user-list: ## List every user account
	docker compose exec -T api node config/list-users.js

user-ban: ## Ban a user for N minutes, e.g. `make user-ban EMAIL=a@b.com MINUTES=60`
	@test -n "$(EMAIL)" && test -n "$(MINUTES)" || \
		{ echo "Usage: make user-ban EMAIL=... MINUTES=..." >&2; exit 1; }
	docker compose exec -T api node config/ban-user.js "$(EMAIL)" "$(MINUTES)"

user-invite: ## Email an invite link instead of setting a password yourself -- needs email sending configured
	@test -n "$(EMAIL)" || { echo "Usage: make user-invite EMAIL=..." >&2; exit 1; }
	docker compose exec -T api node config/invite-user.js "$(EMAIL)"

user-delete: ## Delete a user and ALL their data -- interactive, asks you to confirm (irreversible)
	# This deployment has no Redis, so LibreChat can't coordinate a live
	# generation abort across processes -- delete-user.js additionally
	# asks you to confirm every LibreChat process (this `api` included)
	# is stopped before it'll proceed. Only answer y there if that's true;
	# otherwise stop `api` first (`make down`), then run this.
	docker compose exec api node config/delete-user.js $(EMAIL)

user-reset-password: ## Reset a user's password -- interactive (email + new password prompts)
	docker compose exec api node config/reset-password.js

# ---- Declarative agent management -------------------------------------------
# Agents live in LibreChat's database, not in librechat.yaml. These two
# targets mirror them (definition + handoffs/subagents + sharing) to and
# from agents/*.yaml -- see docs/AGENT_SYNC.md.

agent-export: ## Export every agent + its sharing from the database to agents/*.yaml (replaces those files)
	scripts/agents.sh export

agent-import: ## Sync agents/*.yaml into the database; options: DRY_RUN=1 OWNER_EMAIL= MODEL_PROVIDER= MODEL_NAME= ALLOW_RENAME=1
	DRY_RUN=$(DRY_RUN) OWNER_EMAIL=$(OWNER_EMAIL) MODEL_PROVIDER=$(MODEL_PROVIDER) MODEL_NAME=$(MODEL_NAME) \
		ALLOW_RENAME=$(ALLOW_RENAME) scripts/agents.sh import
