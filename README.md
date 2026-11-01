# AegisAI

[![CI](https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/AnKIT7-ops/AegisAI-AI-Reliability-Platform)](https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

**Measure a RAG application's quality, block regressions in CI and diagnose incidents from evidence, all on your own machine.**

![AegisAI overview page with the four-step guide and status readouts](docs/images/overview.png)

The demo runs with Docker alone. No model, GPU or cloud account is needed.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Measured results](#measured-results)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License and credits](#license-and-credits)

## Features

- **Cited answers.** Ingest documents and ask questions. Answers cite numbered sources, and a question with no relevant source is refused without a model call.
- **Repeatable evaluation.** A versioned 69-example dataset scores retrieval, answers, citations, refusals, prompt injection and latency.
- **Quality gates in CI.** Pull requests run the evaluation with fake model providers, so a pipeline regression fails the build with no GPU.
- **Telemetry.** Structured logs with request IDs, Prometheus metrics and alerts, and a Grafana dashboard.
- **Evidence-based incidents.** Fixed rules, with no model involved, keep observed facts and hypotheses apart. Each incident links a runbook.
- **Simulated releases.** Canary, promote and roll back with an audit trail. A release records a decision and changes no live setting.
- **Local and locked down.** Only nginx is published, on 127.0.0.1 by default, with optional sign-in. See [Security](docs/security.md).

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Compose v2. Commands are for PowerShell.

```powershell
git clone https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform.git aegis-ai
cd aegis-ai
Copy-Item .env.example .env
notepad .env          # set POSTGRES_PASSWORD and GRAFANA_ADMIN_PASSWORD (letters and digits only)
docker compose up -d --build --wait
```

| URL | What |
|---|---|
| http://localhost:8080 | Console |
| http://localhost:8080/api/v1/ | API |
| http://localhost:8080/prometheus/ | Prometheus, which scrapes every 15 s and keeps 7 days. Alerts are at `/alerts` |
| http://localhost:8080/grafana/ | Grafana, *AegisAI overview* dashboard. Viewing needs no login, and the admin password is `GRAFANA_ADMIN_PASSWORD` in `.env` |

To stop the stack:

```powershell
docker compose down        # keeps the data
docker compose down -v     # WARNING: also deletes the database, document index, evaluation reports and metrics history
```

## Usage

### Explore the demo

Open http://localhost:8080 on an empty stack and choose **Load demo data**. Overview then shows a four-step guide:

1. **Ask** opens Chat. Pick a recorded answer to read it with its numbered sources.
2. **Evaluate** opens an evaluation run with its metrics, quality gate result and per-example answers.
3. **Inspect** opens Telemetry: request rate, error rate, latency and firing alerts, with links to Grafana and Prometheus. Values read "No traffic" until Prometheus has scraped some requests, about 30 seconds.
4. **Diagnose** opens a simulated incident, with observed facts and hypotheses kept apart, a runbook and suggested actions.

Releases works from the same demo data: open a release to run a canary, promote or roll back. Everything on the pages comes from the API, and demo data carries a **Demo** or **Simulated** label. A failed request shows its message and its `X-Request-ID`, which finds the request in the [logs](docs/observability.md#logs).

These screenshots come from a fresh clone with demo data and no model running.

| | |
|---|---|
| ![A recorded answer in Chat](docs/images/chat.png) | ![An evaluation run with its quality gate and metrics](docs/images/evaluation.png) |
| Chat | Evaluation run |
| ![Telemetry readouts and firing alerts](docs/images/telemetry.png) | ![An incident with observed facts and hypotheses kept apart](docs/images/incident.png) |
| Telemetry | Incident |
| ![A rejected release showing its gate failures](docs/images/release-rejected.png) | |
| Rejected release | |

### Ask your own questions

This needs [Ollama](https://ollama.com) on the host. Keep it bound to `127.0.0.1:11434`.

```powershell
ollama pull qwen3.5:4b         # chat, ~3.3 GB
ollama pull nomic-embed-text   # embeddings, ~270 MB
.\scripts\ingest_samples.ps1   # 15 pages of Kubernetes and Prometheus documentation
$q = @{ question = "How long does Prometheus keep data by default, and how do I change it?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/v1/chat -ContentType "application/json" -Body $q
```

### Evaluate and diagnose

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/evaluations/run `
  -ContentType 'application/json' -Body '{"label": "baseline"}'
Invoke-RestMethod "http://127.0.0.1:8080/api/v1/evaluations/$($run.id)"    # status, metrics and gate result
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/incidents/detect
```

An evaluation needs Ollama. Incident detection does not.

## How it works

```mermaid
flowchart LR
    browser[Browser or script] -->|127.0.0.1:8080| nginx
    subgraph network[Docker Compose network]
        nginx[nginx: console, gateway, rate limit]
        backend[FastAPI backend]
        postgres[(PostgreSQL)]
        index[(FAISS index)]
        prometheus[Prometheus]
        grafana[Grafana]
        nginx -->|/api/| backend
        nginx -->|/prometheus/| prometheus
        nginx -->|/grafana/| grafana
        backend --> postgres
        backend --> index
        backend -->|alerts and metrics| prometheus
        prometheus -->|scrapes /metrics| backend
        grafana --> prometheus
    end
    backend -->|host.docker.internal:11434| ollama[Ollama on the host]
```

A question goes through nginx to the backend, which embeds it with Ollama, searches the FAISS index and sends the sources to the chat model. Evaluation runs, incidents and release records are stored in PostgreSQL. Prometheus scrapes the backend's metrics and evaluates the alert rules, and incident detection reads those alerts back to build a report.

## Measured results

The 69-example dataset run through the API with `qwen3.5:4b` and `nomic-embed-text` on an RTX 4050 Laptop GPU (6 GB), on 2026-10-28:

| Metric | Baseline |
|---|---|
| Retrieval recall@k | 0.98 |
| Answer correctness | 0.82 (42/51) |
| Citation correctness | 1.00 |
| Correct refusals | 1.00 (10/10) |
| False refusals | 0.06 (3/51) |
| Injection resistance | 0.62 (5/8) |
| Latency p50 / p95 | 0.92 s / 2.8 s |

These figures describe one machine and a small dataset, and they are not a capacity claim. See [Evaluation](docs/evaluation.md) and [Benchmarks](docs/benchmarks.md).

AegisAI is a single-machine system, not a production deployment. It has one shared password, no TLS and in-memory sessions. See [Known limitations](docs/troubleshooting.md#known-limitations).

## Documentation

| Guide | Covers |
|---|---|
| [Models and configuration](docs/configuration.md) | Model choices and every `AEGIS_` setting |
| [Documents, chat and API](docs/api.md) | Ingesting, the endpoint list, how answers stay grounded |
| [Evaluation](docs/evaluation.md) | Dataset, metrics, baseline, quality gate |
| [Observability](docs/observability.md) | Logs, metrics, dashboard, alerts, runbooks |
| [Incidents](docs/incidents.md) | Detection, reports, replaying a scenario |
| [Demo data and simulated releases](docs/releases.md) | Demo records, the release lifecycle |
| [Security](docs/security.md) | Controls, sign-in, exposing beyond localhost |
| [Benchmarks](docs/benchmarks.md) | Latency and resource use, how to reproduce them |
| [CI/CD](docs/ci-cd.md) | Pipeline jobs, the CI gate, versioning |
| [Development](docs/development.md) | Running without Docker, tests, repository layout |
| [Troubleshooting](docs/troubleshooting.md) | Common symptoms and known limitations |

## Contributing

Issues and pull requests are welcome. Open an issue first for a change in behaviour.

1. Branch from `main`. It is protected, so every change lands through a pull request.
2. Run the [lint, type and test commands](docs/development.md#develop-without-docker-powershell) for the code you changed.
3. Open the pull request. The `backend`, `frontend`, `monitoring`, `images (backend)` and `images (frontend)` checks must pass.

Editing the evaluation dataset fails the gate until its limits are measured again. See [Evaluation gate in CI](docs/ci-cd.md#evaluation-gate-in-ci).

## License and credits

The code is under the [MIT License](LICENSE). The sample documents in `data/sample_docs/` keep their own licenses, listed in [ATTRIBUTION.md](data/sample_docs/ATTRIBUTION.md), and that attribution also applies to the document text in `data/demo/`.

The sample corpus comes from the [Kubernetes](https://kubernetes.io) (CC BY 4.0) and [Prometheus](https://prometheus.io) (Apache 2.0) documentation. AegisAI is built on FastAPI, React, PostgreSQL, FAISS, Ollama, Prometheus, Grafana and nginx.
