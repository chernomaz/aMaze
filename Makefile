.PHONY: help dev dev-examples stop build logs test lint fmt typecheck clean

COMPOSE = docker compose
COMPOSE_EXAMPLES = docker compose --profile examples

help:
	@echo "aMaze development commands:"
	@echo ""
	@echo "  make dev           Start core platform services (no example agents/MCPs)"
	@echo "  make dev-examples  Start platform + example MCP containers"
	@echo "  make stop          Stop all services"
	@echo "  make build-base    Build amaze/agent-base:dev and amaze/mcp-base:dev images"
	@echo "  make build         Build all Docker images (runs build-base first)"
	@echo "  make build-agents  Build example agent Docker images"
	@echo "  make build-mcp     Build example MCP tool Docker images"
	@echo "  make logs          Follow logs for all services"
	@echo "  make test          Run tests for all Python services"
	@echo "  make lint          Lint all Python services"
	@echo "  make fmt           Format all Python services"
	@echo "  make typecheck     Type-check all Python services"
	@echo "  make clean         Remove containers, volumes, and workspace data"
	@echo ""
	@echo "URLs (after make dev):"
	@echo "  UI:          http://localhost:3000"
	@echo "  API Gateway: http://localhost:8000"
	@echo "  API Docs:    http://localhost:8000/docs"

dev:
	@cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Platform is starting. UI: http://localhost:3000 | API: http://localhost:8000/docs"

dev-examples:
	@cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE_EXAMPLES) up --build -d
	@echo ""
	@echo "Platform + examples are starting."

stop:
	$(COMPOSE_EXAMPLES) down

build-base:
	docker build -t amaze/agent-base:dev -f agent_runtime/Dockerfile.base .
	docker build -t amaze/mcp-base:dev   -f mcp_runtime/Dockerfile.base .

build: build-base
	$(COMPOSE) build

build-agents: build-base
	$(COMPOSE) build echo-agent summarizer-agent researcher-agent reviewer-agent

build-mcp: build-base
	$(COMPOSE) --profile examples build mcp-filesystem mcp-websearch mcp-calculator mcp-counter

logs:
	$(COMPOSE) logs -f

# ─── Per-service test / lint / format targets ─────────────────────────────────

PYTHON_SERVICES = shared services/api_gateway services/orchestrator services/proxy services/registry services/policy_engine

test:
	@for svc in $(PYTHON_SERVICES); do \
		echo "\n=== Testing $$svc ==="; \
		cd $$svc && uv run pytest -q && cd -; \
	done

lint:
	@for svc in $(PYTHON_SERVICES); do \
		echo "\n=== Linting $$svc ==="; \
		cd $$svc && uv run ruff check . && cd -; \
	done

fmt:
	@for svc in $(PYTHON_SERVICES); do \
		echo "\n=== Formatting $$svc ==="; \
		cd $$svc && uv run ruff format . && cd -; \
	done

typecheck:
	@for svc in $(PYTHON_SERVICES); do \
		echo "\n=== Type-checking $$svc ==="; \
		cd $$svc && uv run mypy src/ && cd -; \
	done

clean:
	$(COMPOSE_EXAMPLES) down -v --remove-orphans
	rm -rf /tmp/amaze-workspaces
	@echo "Cleaned up containers, volumes, and workspace data."
