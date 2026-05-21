.PHONY: up down logs psql redis-cli seed test demo

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f $(if $(SERVICE),$(SERVICE),)

psql:
	docker compose exec postgres psql -U olive olive

redis-cli:
	docker compose exec redis redis-cli

seed:
	@echo "seed: not implemented until Phase 4"

test:
	@echo "test: not implemented until Phase 2"

demo:
	$(MAKE) up
	@echo ""
	@echo "Chatbot UI  → http://localhost:3000"
	@echo "Dashboard   → http://localhost:3000/metrics"
	@echo "Chatbot API → http://localhost:8000/docs"
	@echo "Ingestion   → http://localhost:8001/docs"
