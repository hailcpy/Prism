.PHONY: up down nuke restart init logs psql redis-cli seed install-dev lint format format-check typecheck test check demo web-rebuild web-restart web-logs web-dev web-dev-stop web-install

# `up` builds images and starts containers in the background.
# Use this after a Dockerfile/lockfile change, or when adding/removing services.
up:
	docker compose up -d --build

# `down` stops and removes containers but KEEPS named volumes
# (postgres-data, redis-data). Your data survives.
down:
	docker compose down --remove-orphans
	@$(MAKE) --no-print-directory web-dev-stop

web-dev-stop:
	@pid=$$(lsof -ti tcp:3000); \
	if [ -n "$$pid" ]; then \
	  echo "Killing local next dev on :3000 (pid $$pid)"; \
	  kill $$pid 2>/dev/null || true; \
	fi

# `nuke` ALSO deletes named volumes — postgres + redis data is gone.
# Requires explicit confirmation. Use this only when you want a clean slate.
nuke:
	@echo "This will DELETE all postgres and redis data (named volumes)."
	@printf "Type 'nuke' to confirm: "; read ans; [ "$$ans" = "nuke" ] || (echo "Aborted."; exit 1)
	docker compose down -v

# `restart` reloads running services. With the source bind-mounts in
# docker-compose.yml, this picks up Python code changes WITHOUT a rebuild.
# Pass SERVICE=name to restart just one service.
restart:
	docker compose restart $(if $(SERVICE),$(SERVICE),)

init:
	docker compose restart

logs:
	docker compose logs -f $(if $(SERVICE),$(SERVICE),)

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-prism} $${POSTGRES_DB:-prism}

redis-cli:
	docker compose exec redis sh -c 'redis-cli -a "$$REDIS_PASSWORD"'

seed:
	@echo "seed: not implemented until Phase 4"

install-dev:
	uv sync --all-packages --dev

lint:
	uv run ruff check .
	cd web && npm run lint

format:
	uv run ruff check --select I --fix .
	uv run ruff format .
	cd web && npm run format

format-check:
	uv run ruff format --check .
	cd web && npm run format:check

typecheck:
	uv run ty check
	cd web && npm run typecheck

test:
	uv run pytest

check: lint format-check typecheck test

demo:
	$(MAKE) up
	@echo ""
	@echo "Chatbot UI  → http://localhost:3001"
	@echo "Dashboard   → http://localhost:3001/metrics"
	@echo "Chatbot API → http://localhost:8100/docs"
	@echo "Ingestion   → http://localhost:8101/docs"

# ── Frontend ────────────────────────────────────────────────
# web-rebuild   : rebuild the web Docker image and restart it (needed
#                 after any change under web/ — no bind-mount exists)
# web-restart   : restart the web container without rebuilding
# web-logs      : tail logs from the web container
# web-install   : install npm deps locally in ./web
# web-dev       : run Next dev server LOCALLY on http://localhost:3000.
#                 Note: this is separate from the Docker UI on :3001.
#                 Stop the Docker web service first (or just use one).

web-rebuild:
	docker compose build chatbot-ui
	docker compose up -d chatbot-ui
	@echo "Web rebuilt → http://localhost:3001"

web-restart:
	docker compose restart chatbot-ui

web-logs:
	docker compose logs -f chatbot-ui

web-install:
	cd web && npm install

web-dev:
	@echo "Starting local Next dev server on http://localhost:3000"
	@echo "(Docker UI on :3001 is unaffected — stop it with 'docker compose stop web' if you only want one.)"
	cd web && npm run dev
