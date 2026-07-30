FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
COPY model_gateway/pyproject.toml ./model_gateway/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package neomua-model-gateway --no-install-workspace
COPY model_gateway/model_gateway ./model_gateway/model_gateway
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package neomua-model-gateway

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"
CMD ["python", "-m", "uvicorn", "model_gateway.app:app", "--host", "0.0.0.0", "--port", "8090"]
