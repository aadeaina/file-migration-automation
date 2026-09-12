# Reuses the main project's dependency set (grpcio + temporalio are
# already there) rather than maintaining a second lockfile for what's
# really the same runtime.
FROM python:3.13-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY keda-scaler/ keda-scaler/

ENV PATH="/app/.venv/bin:${PATH}"
WORKDIR /app/keda-scaler

CMD ["python", "server.py"]
