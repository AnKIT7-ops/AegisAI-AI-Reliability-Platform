# Error-rate spike

Alerts: `AegisHighErrorRate` (more than 5% of API requests return 5xx for 2 minutes), `AegisModelFailures` (3 or more failed Ollama calls in 5 minutes), `AegisBackendDown` (Prometheus cannot scrape the backend for 1 minute). Incident type: `elevated_api_error_rate`.

## What you see

- Grafana, *AegisAI overview*: **5xx error ratio** turns orange or red; **5xx responses by route** shows the failing route; **Model calls by outcome** shows a non-`ok` outcome.
- Clients get `503 {"error": {"code": "model_unavailable", ...}}` (the model failed) or `500 internal_error` (an unexpected error).

## Find the cause

1. Which outcome? In Prometheus (http://localhost:8080/prometheus) or Grafana:

   ```promql
   sum by (operation, outcome) (increase(aegis_model_requests_total{outcome!="ok"}[15m]))
   ```

   | Outcome | Meaning | Usual fix |
   |---|---|---|
   | `unreachable` | Ollama is not running or not listening on 127.0.0.1:11434 | Start Ollama; check `curl.exe http://127.0.0.1:11434/api/version` |
   | `timeout` | A call exceeded `AEGIS_GENERATE_TIMEOUT_S` (default 120 s) | See [latency spike](latency-spike.md) |
   | `model_missing` | The configured model is not pulled | `ollama list`; pull the model named in `GET /api/v1/models` |
   | `http_error`, `bad_response` | Ollama answered with an error or an unexpected body | Ollama logs; restart Ollama |
   | `injected` | Fault injection is on | Set `AEGIS_FAULT_ERROR_RATE=0` (see below) |

2. Read the failing requests. Every response carries `X-Request-ID`; the log lines for one request share it:

   ```powershell
   docker compose logs --no-log-prefix backend | Select-String '"status": 5\d\d' | Select-Object -Last 5
   docker compose logs --no-log-prefix backend | Select-String '"request_id": "<id from above>"'
   ```

   `"event": "model_error"` lines name the model failure; `"event": "error"` lines carry the stack trace of an unexpected 500.

3. `AegisBackendDown`: `docker compose ps backend`, then `docker compose logs backend --tail 50`. The backend is unhealthy while PostgreSQL is unreachable or a migration fails.

## Reproduce it (drill)

Fault injection makes a chosen share of Ollama calls fail with outcome `injected`. It is off unless set, and the backend logs a warning at startup while it is on.

```powershell
$env:AEGIS_FAULT_ERROR_RATE = "1"          # every model call fails
docker compose up -d --wait backend
1..30 | ForEach-Object {
  try {
    Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/chat -ContentType 'application/json' `
      -Body '{"question": "How long does Prometheus keep data by default?"}' | Out-Null
  } catch { }                              # the 503s are the point
  Start-Sleep 5
}
```

Expected: within a minute **Failed model calls** passes 3 and `AegisModelFailures` fires; after about 2 more minutes `AegisHighErrorRate` fires (http://localhost:8080/prometheus/alerts). Responses are `503 model_unavailable` with the message `injected fault (AEGIS_FAULT_ERROR_RATE)`.

## Clean up

```powershell
Remove-Item Env:AEGIS_FAULT_ERROR_RATE
docker compose up -d --wait backend
```

The alerts resolve once the 5-minute windows no longer contain failures. Check that the startup warning `fault injection is ON` is gone from `docker compose logs backend`.
