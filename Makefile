.PHONY: up down init logs psql redis-cli seed install-dev lint format format-check typecheck test check demo web-rebuild web-restart web-logs web-dev web-install

up:
	docker compose up -d --build

down:
	docker compose down

init:
	docker compose restart

logs:
	docker compose logs -f $(if $(SERVICE),$(SERVICE),)

psql:
	docker compose exec postgres psql -U prism prism

redis-cli:
	docker compose exec redis redis-cli

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
