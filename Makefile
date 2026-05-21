.PHONY: up down logs psql redis-cli seed install-dev lint format format-check typecheck test check demo

up:
	docker compose up -d --build

down:
	docker compose down

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
	@echo "Chatbot UI  → http://localhost:3000"
	@echo "Dashboard   → http://localhost:3000/metrics"
	@echo "Chatbot API → http://localhost:8000/docs"
	@echo "Ingestion   → http://localhost:8001/docs"
