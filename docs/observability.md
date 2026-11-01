# Observability

## Logs

The backend writes one JSON object per line to stdout (`docker compose logs backend`):

```json
{"ts": "2026-10-28T14:35:28.955+00:00", "level": "info", "logger": "aegis.access", "message": "POST /api/v1/chat 200", "request_id": "live-check-1", "event": "request", "method": "POST", "path": "/api/v1/chat", "status": 200, "duration_ms": 2120.1}
```

- Every response carries an `X-Request-ID` header, and every log line written while handling the request (including an evaluation run it starts) carries the same `request_id`. A caller can supply its own ID (letters, digits, `.`, `_`, `-`, up to 64 characters); anything else is replaced.
- Events: `request` (method, path without query string, status, duration), `chat` (question length, `top_k`, number of sources, refused, model, latency, token counts), `ingest` (document id, chunks, size), `model_error`, `evaluation` (run started and finished), `error` (an unexpected exception, with its stack trace; the client gets a 500 that still carries `X-Request-ID`).
- Questions, answers, prompts, document text and file names are never logged. Passwords in URLs, `Bearer` tokens and values of keys such as `password`, `POSTGRES_PASSWORD`, `access_token`, `api_key` or `client_secret` (also quoted JSON keys) are masked in messages and stack traces. Extra log fields are limited to a fixed list, so a careless log call cannot add request bodies.
- Health-check requests log at `debug`.

Find everything about one request (PowerShell):

```powershell
docker compose logs --no-log-prefix backend | Select-String '"request_id": "live-check-1"'
```

## Prometheus metrics

`GET /metrics` serves Prometheus text format; the `prometheus` service in Compose scrapes it every 15 s. Every label has a small fixed set of values: routes are templates such as `/api/v1/evaluations/{run_id}`, never raw paths, and unknown or rejected requests share `route="unmatched"`.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `aegis_http_requests_total` | counter | `method`, `route`, `status` | Requests, including 4xx and 5xx |
| `aegis_http_request_duration_seconds` | histogram | `method`, `route` | End-to-end request time, model calls included |
| `aegis_model_requests_total` | counter | `operation` (`chat`, `embed`, `tags`), `outcome` | Ollama calls; `outcome` is `ok`, `timeout`, `unreachable`, `model_missing`, `http_error`, `bad_response` (a reply that is not in the expected shape) or `injected` (fault injection drills) |
| `aegis_model_request_duration_seconds` | histogram | `operation` | Ollama call time |
| `aegis_model_tokens_total` | counter | `kind` (`prompt`, `completion`) | Tokens reported by the chat model |
| `aegis_retrieval_duration_seconds` | histogram | | Query embedding plus vector search |
| `aegis_chat_answers_total` | counter | `outcome` (`answered`, `refused_low_score`, `refused_by_model`) | `/api/v1/chat` results (evaluation runs are not counted here) |
| `aegis_documents_ingested_total` | counter | `result` (`created`, `duplicate`) | Ingest requests that stored or matched a document |
| `aegis_evaluation_runs_total` | counter | `status` (`done`, `failed`) | Finished evaluation runs |
| `aegis_evaluation_last_metric` | gauge | `metric` (`recall_at_k`, `answer_correctness`, ...) | Metrics of the most recent finished run, whatever its label |
| `aegis_evaluation_last_gate_passed` | gauge | | 1 if that run passed its quality gate, 0 if it failed (an unreadable or invalid thresholds file counts as failed), NaN if no thresholds file exists |

Histogram buckets run from 50 ms to 120 s (the generation timeout); retrieval buckets from 10 ms to 5 s. Counters reset when the backend restarts, which `rate()` and `increase()` handle.

Useful queries (Prometheus UI at http://localhost:8080/prometheus):

```promql
sum by (route, status) (rate(aegis_http_requests_total[5m]))                      # traffic and errors
histogram_quantile(0.95, sum by (le, route) (rate(aegis_http_request_duration_seconds_bucket[5m])))
sum by (outcome) (increase(aegis_model_requests_total[15m]))                      # model failures by cause
```

## Dashboard

Grafana loads the *AegisAI overview* dashboard from `monitoring/grafana/dashboards/aegis-overview.json` and Prometheus as its data source from `monitoring/grafana/provisioning/`; both are read-only in the container, so change the files rather than the UI. Rows:

| Row | Panels |
|---|---|
| Service | Request rate (req/s), 5xx error ratio, chat p95 latency (s), failed model calls in 5 min, last evaluation gate, last answer correctness |
| Traffic and errors | Requests by route and status; 5xx responses by route |
| Latency | Request p50/p95 by route; model-call p95 by operation and retrieval p95 |
| Model | Model calls by outcome; tokens per second; chat outcomes |
| Evaluation | Quality metrics of the latest run; finished runs per hour |

## Alerts

Rules live in `monitoring/prometheus/alerts.yml`; each names its runbook. Prometheus evaluates them every 15 s and shows them at http://localhost:8080/prometheus/alerts (no Alertmanager or notifications are configured).

| Alert | Fires when | Severity | Runbook |
|---|---|---|---|
| `AegisBackendDown` | The backend cannot be scraped for 1 min | critical | [error-rate spike](runbooks/error-rate-spike.md) |
| `AegisHighErrorRate` | More than 5% of API requests (health checks and scrapes excluded) return 5xx for 2 min | critical | [error-rate spike](runbooks/error-rate-spike.md) |
| `AegisModelFailures` | 3 or more Ollama calls fail within 5 min | warning | [error-rate spike](runbooks/error-rate-spike.md) |
| `AegisChatLatencyHigh` | Chat p95 above 10 s for 2 min (baseline about 2.8 s) | warning | [latency spike](runbooks/latency-spike.md) |
| `AegisEvaluationGateFailed` | The latest evaluation run failed its quality gate | warning | [evaluation gate failed](runbooks/evaluation-gate-failed.md) |

The thresholds are tested with synthetic series (`monitoring/prometheus/alerts.test.yml`):

```powershell
docker compose exec -w /etc/prometheus prometheus promtool test rules alerts.test.yml
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
```

Tracing (OpenTelemetry or Langfuse) is not set up yet; logs with request IDs and the metrics above cover diagnosis for now.

## Runbooks

Each runbook in `docs/runbooks/` covers what the alert means, how to find the cause with the dashboard, metrics and logs, and a drill that reproduces it with fault injection:

- [Error-rate spike](runbooks/error-rate-spike.md): `AEGIS_FAULT_ERROR_RATE=1` makes every model call fail; 30 chat requests fire `AegisModelFailures` and `AegisHighErrorRate`.
- [Latency spike](runbooks/latency-spike.md): `AEGIS_FAULT_DELAY_MS=6000` adds 6 s to each model call; 12 chat requests fire `AegisChatLatencyHigh`.
- [Evaluation gate failed](runbooks/evaluation-gate-failed.md): a degraded evaluation run fails the gate.

Both fault drills were run against this stack. In the error drill all 30 chat requests returned `503 model_unavailable` and the dashboard's 5xx ratio read 0.96; in the latency drill each chat took 12.5 to 13.8 s. The alerts fired in both, and all of them resolved within 5 minutes of turning fault injection off.

## Telemetry summary

`GET /api/v1/telemetry` reads Prometheus and checks the database. A null value means no traffic in the last 5 minutes, never zero, and anything unreadable is named in `not_checked`. It does not probe Ollama, because a failed probe would itself count as a model failure.
