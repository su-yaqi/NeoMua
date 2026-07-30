FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
COPY workflow_runtime/pyproject.toml ./workflow_runtime/
COPY runtime_worker/pyproject.toml ./runtime_worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package neomua-runtime-worker --extra claude --no-install-workspace
COPY runtime_worker/runtime_worker ./runtime_worker/runtime_worker
COPY workflow_runtime/workflow_runtime ./workflow_runtime/workflow_runtime
COPY workflow_apps ./workflow_apps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package neomua-runtime-worker --extra claude

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import os; os.kill(1, 0)"
CMD ["python", "-m", "runtime_worker.main"]
