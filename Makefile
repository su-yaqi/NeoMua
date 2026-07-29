SHELL := /bin/bash

DEV_SLOT ?= 0
DEV_PROJECT_SCOPE ?= neomua
DEV_STACK := DEV_SLOT="$(DEV_SLOT)" DEV_PROJECT_SCOPE="$(DEV_PROJECT_SCOPE)" ./scripts/dev-stack.sh

.PHONY: help check-change test-change-process dev-init dev-preflight dev-up dev-down dev-restart dev-ps dev-logs dev-logs-backend dev-logs-frontend dev-watch dev-health dev-config dev-test-config dev-urls dev-tools-up dev-tools-down dev-proxy-up dev-proxy-down dev-db-shell test-backend test-e2e generate-client frontend-hot-up frontend-hot-down frontend-hot-logs frontend-static-up

help:
	@echo "Available targets:"
	@echo "  make check-change      - Validate S/M/H risk and process evidence"
	@echo "  make test-change-process - Test the S/M/H process checker"
	@echo "  make dev-init          - Create a local .env without overwriting an existing one"
	@echo "  make dev-preflight     - Check this slot's project ownership and core ports"
	@echo "  make dev-up            - Build and start the isolated core stack"
	@echo "  make dev-down          - Stop only this isolated stack"
	@echo "  make dev-restart       - Restart only this isolated stack"
	@echo "  make dev-ps            - Show services status"
	@echo "  make dev-logs          - Follow logs for all services"
	@echo "  make dev-logs-backend  - Follow backend logs"
	@echo "  make dev-logs-frontend - Follow frontend logs"
	@echo "  make dev-watch         - Start stack in watch mode"
	@echo "  make dev-health        - Check backend health endpoint inside container"
	@echo "  make dev-config        - Validate the rendered development Compose config"
	@echo "  make dev-test-config   - Validate the isolated test Compose config"
	@echo "  make dev-urls          - Print deterministic addresses for this slot"
	@echo "  make dev-tools-up      - Start optional Adminer and Mailcatcher"
	@echo "  make dev-tools-down    - Stop optional Adminer and Mailcatcher"
	@echo "  make dev-proxy-up      - Start the optional local Traefik proxy"
	@echo "  make dev-proxy-down    - Stop the optional local Traefik proxy"
	@echo "  make dev-db-shell      - Open psql inside this slot's database"
	@echo "  make test-backend      - Run backend pytest in container"
	@echo "  make test-e2e          - Run playwright tests in playwright container"
	@echo "  make generate-client   - Regenerate frontend SDK from backend OpenAPI"
	@echo "  make frontend-hot-up   - Switch frontend to Docker Vite hot-reload mode"
	@echo "  make frontend-hot-down - Stop Docker Vite hot-reload frontend"
	@echo "  make frontend-hot-logs - Follow Docker Vite hot-reload frontend logs"
	@echo "  make frontend-static-up - Switch frontend back to stable static mode"
	@echo ""
	@echo "Select another deterministic instance with: make DEV_SLOT=1 dev-up"

check-change:
	@./scripts/check-change-process.sh

test-change-process:
	@./scripts/tests/test-check-change-process.sh

dev-init:
	@$(DEV_STACK) init

dev-preflight:
	@$(DEV_STACK) preflight

# Core stack only. Optional tools, proxy, and tests use explicit targets/profiles.
dev-up:
	@$(DEV_STACK) up

dev-down:
	@$(DEV_STACK) down

dev-restart:
	@$(DEV_STACK) restart

dev-ps:
	@$(DEV_STACK) ps

dev-logs:
	@$(DEV_STACK) logs

dev-logs-backend:
	@$(DEV_STACK) logs backend

dev-logs-frontend:
	@$(DEV_STACK) logs frontend

dev-watch:
	@$(DEV_STACK) watch

dev-health:
	@$(DEV_STACK) health

dev-config:
	@$(DEV_STACK) config --quiet

dev-test-config:
	@$(DEV_STACK) test-config --quiet

dev-urls:
	@$(DEV_STACK) urls

dev-tools-up:
	@$(DEV_STACK) tools-up

dev-tools-down:
	@$(DEV_STACK) tools-down

dev-proxy-up:
	@$(DEV_STACK) proxy-up

dev-proxy-down:
	@$(DEV_STACK) proxy-down

dev-db-shell:
	@$(DEV_STACK) db-shell

test-backend:
	@$(DEV_STACK) test-backend

test-e2e:
	@$(DEV_STACK) test-e2e

generate-client:
	bash ./scripts/generate-client.sh

frontend-hot-up:
	@$(DEV_STACK) frontend-hot-up

frontend-hot-down:
	@$(DEV_STACK) frontend-hot-down

frontend-hot-logs:
	@$(DEV_STACK) frontend-hot-logs

frontend-static-up:
	@$(DEV_STACK) frontend-static-up
