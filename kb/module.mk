# The knowledge base's make targets. The root Makefile includes this file; every
# recipe runs from the repository root, where `docker compose` lives.

.PHONY: kb-init kb-setup kb-check kb-test kb-sync kb-sync-all kb-sources kb-sources-discover kb-status

kb-init: ## Create/upgrade the knowledge base schema + its read-only role (idempotent)
	kb/scripts/init.sh

kb-setup: ## Bring the knowledge base up and report what is still needed (runs inside `make up` too)
	# The env file and source configuration must exist before any `docker compose`
	# command: the compose file interpolates from kb/.env.kb, and kb-scheduler
	# bind-mounts kb/config/sources.yaml (Docker would create the mountpoint as an
	# empty directory when the file doesn't exist yet).
	kb/scripts/prepare.sh
	# --build, because a kb/ change that isn't in the image is a service quietly
	# running last week's code -- which looks like "it stopped syncing".
	docker compose up -d --build kb-db mcp-kb kb-scheduler
	kb/scripts/bootstrap.sh

kb-sync: ## Sync one source into the catalog, e.g. `make kb-sync SOURCE=confluence-eng [MODE=incremental] [DRY_RUN=1]`
	@test -n "$(SOURCE)" || { echo "Usage: make kb-sync SOURCE=<id from kb/config/sources.yaml>" >&2; exit 1; }
	docker compose run --rm kb-cli sync --source $(SOURCE) --mode $(or $(MODE),full) $(if $(DRY_RUN),--dry-run,)

kb-sync-all: ## Full-sync every source enabled in kb/config/sources.yaml, one at a time; FORCE=1 bypasses the content-hash gate
	kb/scripts/sync-all.sh $(if $(FORCE),--force,)

kb-sources: ## List the configured sources and whether each is enabled
	docker compose run --rm kb-cli sources

kb-sources-discover: ## Propose sources from what Confluence/Jira actually contain; WRITE=1 appends them (disabled) for review
	docker compose run --rm kb-cli discover $(if $(WRITE),--write,)

kb-check: ## Is everything the catalog depends on actually usable (database, summarizer, sources)
	docker compose run --rm kb-cli check

kb-status: ## What the catalog contains right now
	docker compose run --rm kb-cli status

kb-test: ## Run kb's own suite, including the Postgres-backed tests, against a throwaway database
	kb/scripts/test.sh
