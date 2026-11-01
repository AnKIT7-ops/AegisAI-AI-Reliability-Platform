FROM python:3.12.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 aegis \
    && mkdir -p /app/data/index /app/data/runs && chown aegis /app/data/index /app/data/runs
USER aegis
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# 0.0.0.0 is inside the container only; docker-compose.yml does not publish this port.
# Migrations run first; uvicorn only starts on a schema at head.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
