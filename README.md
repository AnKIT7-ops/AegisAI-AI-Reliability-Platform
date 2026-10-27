# AegisAI — AI Reliability & Operations Platform

A local-first platform for building, evaluating, observing and operating a RAG application. The engineering around the model is the point: repeatable evaluation, telemetry, CI quality gates and runbook-driven incident workflows.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation: FastAPI, React, PostgreSQL, Compose, health checks | Done |
| 1 | RAG: ingestion, chunking, FAISS retrieval, Ollama provider, cited answers | Next |
| 2 | Evaluation harness: retrieval, answer quality, citation support, latency | Planned |
| 3 | Observability: metrics, structured logs, traces | Planned |
| 4 | CI/CD with AI quality gates | Planned |
| 5 | Incident detection and runbook-driven diagnosis | Planned |
| 6 | Operations dashboard and release workflow | Planned |
| 7 | Hardening and benchmarks | Planned |

## Prerequisites

| Tool | Version used |
|---|---|
| Docker Desktop (Compose v2) | Docker 29.8, Compose 5.5 |
| uv (manages Python 3.12) | 0.12.15 |
| Node.js / npm | Node 24+ / npm 11 |
| Git | 2.53 |

## Run with Docker Compose (PowerShell)

```powershell
git clone https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform.git aegis-ai
cd aegis-ai
Copy-Item .env.example .env
notepad .env          # set POSTGRES_PASSWORD (letters and digits only)
docker compose up -d --build --wait
```

| URL | What |
|---|---|
| http://127.0.0.1:8080 | Frontend |
| http://127.0.0.1:8000/health/live | Backend liveness (process up) |
| http://127.0.0.1:8000/health/ready | Backend readiness (503 until Postgres is reachable) |
| http://127.0.0.1:8000/docs | API docs |

Every port binds to 127.0.0.1. Postgres is not published to the host. Startup order is enforced with healthchecks: postgres healthy → backend healthy → frontend.

## Develop without Docker (PowerShell)

Backend:
```powershell
cd backend
uv sync
uv run pytest -q
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app tests
uv run uvicorn app.main:app --reload   # /health/ready returns 503 unless DATABASE_URL points at a running Postgres
```

Frontend:
```powershell
cd frontend
npm ci
npm run dev       # http://localhost:5173
npm run lint ; npm run build
```

## Stop and clean up

```powershell
docker compose down        # stops containers and keeps the database volume
docker compose down -v     # WARNING: also deletes the pgdata volume (all database data)
```

## Layout

```text
backend/    FastAPI app (app/), tests (tests/), uv project
frontend/   React + TypeScript + Vite
```
