SHELL := /bin/bash

.PHONY: help dev-up dev-down dev-restart dev-ps dev-logs dev-logs-backend dev-logs-frontend dev-watch dev-health test-backend test-e2e generate-client frontend-hot-up frontend-hot-down frontend-hot-logs frontend-static-up

help:
	@echo "Available targets:"
	@echo "  make dev-up            - Build and start full docker dev stack"
	@echo "  make dev-down          - Stop stack and remove containers"
	@echo "  make dev-restart       - Restart full stack"
	@echo "  make dev-ps            - Show services status"
	@echo "  make dev-logs          - Follow logs for all services"
	@echo "  make dev-logs-backend  - Follow backend logs"
	@echo "  make dev-logs-frontend - Follow frontend logs"
	@echo "  make dev-watch         - Start stack in watch mode"
	@echo "  make dev-health        - Check backend health endpoint inside container"
	@echo "  make test-backend      - Run backend pytest in container"
	@echo "  make test-e2e          - Run playwright tests in playwright container"
	@echo "  make generate-client   - Regenerate frontend SDK from backend OpenAPI"
	@echo "  make frontend-hot-up   - Switch frontend to Docker Vite hot-reload mode"
	@echo "  make frontend-hot-down - Stop Docker Vite hot-reload frontend"
	@echo "  make frontend-hot-logs - Follow Docker Vite hot-reload frontend logs"
	@echo "  make frontend-static-up - Switch frontend back to stable static mode"

# Full local dev stack (backend, frontend, db, adminer, mailcatcher, traefik, playwright)
dev-up:
	docker compose up -d --build

dev-down:
	docker compose down --remove-orphans

dev-restart: dev-down dev-up

dev-ps:
	docker compose ps

dev-logs:
	docker compose logs -f

dev-logs-backend:
	docker compose logs -f backend

dev-logs-frontend:
	docker compose logs -f frontend

dev-watch:
	docker compose watch

dev-health:
	docker compose exec -T backend curl -sS http://localhost:8000/api/v1/utils/health-check/

test-backend:
	docker compose exec -T backend bash scripts/tests-start.sh

test-e2e:
	docker compose --profile test run --rm --build playwright bunx playwright test

generate-client:
	bash ./scripts/generate-client.sh

frontend-hot-up:
	docker compose stop frontend || true
	docker compose --profile dev-hot up -d --build frontend-dev

frontend-hot-down:
	docker compose --profile dev-hot stop frontend-dev

frontend-hot-logs:
	docker compose --profile dev-hot logs -f frontend-dev

frontend-static-up:
	docker compose --profile dev-hot stop frontend-dev || true
	docker compose up -d frontend
