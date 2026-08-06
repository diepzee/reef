FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
COPY piccolo_conf.py ./
RUN uv sync --frozen --no-dev
ENV PYTHONPATH=/app/src
# migrate.py wraps `piccolo migrations forwards` in an advisory lock, so two
# containers booting at once cannot run the same migration concurrently.
CMD ["sh", "-c", "uv run python scripts/migrate.py && uv run python -m rif.server"]
