# Development

## Prerequisites

| Tool | Version used |
|---|---|
| Docker Desktop (Compose v2) | Docker 29.8, Compose 5.5 |
| uv (manages Python 3.12) | 0.12.15 |
| Node.js / npm | Node 24+ / npm 11 |
| Git | 2.53 |
| Ollama (runs on the host) | 0.40.11 |

## Reach the internal services

nginx is the only published service, on 127.0.0.1 by default. It serves the frontend, proxies `/api/`, `/prometheus/` and `/grafana/`, and refuses request bodies over 2 MB. The backend, Prometheus, Grafana and Postgres are reachable only on the Docker network, so `/health/*`, `/metrics` and `/docs` return 404 from the host. To reach them:

```powershell
docker compose ps    # health of every service
docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read())"
docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())"
```

Startup order is enforced with healthchecks: postgres healthy → backend healthy → frontend. The backend applies database migrations (`alembic upgrade head`) before it starts serving.

## Develop without Docker (PowerShell)

Backend:
```powershell
cd backend
uv sync
uv run pytest -q
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app tests alembic
$env:DATABASE_URL = "postgresql://..." ; uv run alembic upgrade head   # needs a running Postgres
uv run uvicorn app.main:app --reload   # /health/ready returns 503 unless DATABASE_URL points at a running Postgres
```

Frontend:
```powershell
cd frontend
npm ci
npm run dev       # http://localhost:5173, using the running stack for /api
npm test ; npm run lint ; npm run build
```

## Browser tests

`frontend/e2e/` drives the console against a separate Compose project on port 8090, so your own stack and data are untouched. It needs no model. The tests start from an empty database, so reset the project before each run:

```powershell
docker compose -p aegis-e2e --env-file frontend/e2e/e2e.env down -v
docker compose -p aegis-e2e --env-file frontend/e2e/e2e.env up -d --build --wait
cd frontend
npx playwright install chromium    # or: $env:AEGIS_E2E_BROWSER = "<path to a Chromium-based browser>"
npm run e2e
```

They cover the first-time journey, repeat demo loading, promotion, a refused promotion, rollback, a live evaluation through to a resolved incident, sign-in and session end, the nginx boundary, keyboard access to the content, and sideways scrolling at 360 px. Remove the project with `docker compose -p aegis-e2e --env-file frontend/e2e/e2e.env down -v`.

## Reproduce the results

| Result | How |
|---|---|
| Unit and API tests | `uv run pytest -q` in `backend/`, `npm test` in `frontend/`. No model needed |
| Evaluation gate, fake model | [Evaluation gate in CI](ci-cd.md#evaluation-gate-in-ci). No model needed |
| Evaluation, real model | [Run an evaluation](evaluation.md#run-an-evaluation-powershell). Compare the dataset checksum first |
| Browser journeys | [Browser tests](#browser-tests). No model needed |
| Latency and resource use | [Benchmarks](benchmarks.md). Expect different figures on other hardware |
| Incidents | [Reproduce an incident](incidents.md#reproduce-an-incident) replays a recorded snapshot. Each [runbook](observability.md#runbooks) has a live drill |

## Stop and clean up

```powershell
docker compose down        # stops containers and keeps the database volume
docker compose down -v     # WARNING: also deletes the pgdata, ragindex, evalruns and promdata volumes (database, document index, evaluation reports, metrics history)
```

Setup and teardown were checked from a fresh clone on 2026-11-01. `docker compose up -d --build --wait` took 63 s with image layers already cached, and `docker compose down -v` left no containers, volumes or networks. The built images stay until you remove them with `docker image rm`.

## Layout

```text
.github/    CI workflow
backend/    FastAPI app (app/: api, core, db, services/llm, services/rag, services/evaluation, services/incidents), alembic/ migrations, tests (unit/, integration/, evaluation/), uv project
frontend/   React + TypeScript + Vite console (src/: pages, components), e2e/ browser tests, nginx.conf for the gateway
data/       sample_docs/ corpus with attribution; evaluation/ datasets and quality thresholds; incidents/ recorded scenarios; demo/ exported demo data
monitoring/ Prometheus configuration and alert rules, Grafana provisioning and dashboard
docs/       guides linked from the README; runbooks/ for each alert; benchmarks/ saved results; images/ screenshots
scripts/    Ingest, demo export and benchmark helpers
```
