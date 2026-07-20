FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
COPY workflow_runtime/pyproject.toml ./workflow_runtime/
COPY workflow_runtime/workflow_runtime ./workflow_runtime/workflow_runtime
COPY workflow_apps ./workflow_apps
COPY runtime_worker/pyproject.toml ./runtime_worker/
COPY runtime_worker/runtime_worker ./runtime_worker/runtime_worker
COPY node_runtime/pyproject.toml ./node_runtime/
COPY node_runtime/node_runtime ./node_runtime/node_runtime
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package neomua-node-runtime --extra service

ENTRYPOINT ["neomua-node"]
CMD ["--help"]
