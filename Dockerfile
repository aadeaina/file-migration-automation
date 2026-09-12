FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first for layer caching -- code changes shouldn't force a
# full dependency reinstall.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    DJANGO_SETTINGS_MODULE=config.settings \
    PYTHONPATH=/app

ENTRYPOINT []
CMD ["python", "-m", "migration.orchestration.worker"]
