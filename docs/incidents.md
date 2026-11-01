# Incidents

`POST /api/v1/incidents/detect` runs one detection pass and returns an evidence-backed report for each problem it finds. Diagnosis is fixed rules, with no model involved, so the same signals always give the same report.

| Incident | Detected from | Severity | Runbook |
|---|---|---|---|
| `model_timeout` | `AegisModelFailures` with outcome `timeout` | warning | [model timeout](runbooks/model-timeout.md) |
| `model_unavailable` | `AegisModelFailures` with any other outcome | critical | [model unavailable](runbooks/model-unavailable.md) |
| `increased_latency` | `AegisChatLatencyHigh` | warning | [latency spike](runbooks/latency-spike.md) |
| `elevated_api_error_rate` | `AegisHighErrorRate` | critical | [error-rate spike](runbooks/error-rate-spike.md) |
| `retrieval_quality_regression` | The latest finished evaluation run failed its gate | warning | [evaluation gate failed](runbooks/evaluation-gate-failed.md) |
| `unhealthy_dependency` | `SELECT 1` on PostgreSQL fails | critical | [unhealthy dependency](runbooks/unhealthy-dependency.md) |

- **One source of limits.** A firing Prometheus alert is the breach, so thresholds stay in `alerts.yml`. Pending alerts do not count.
- **Facts and hypotheses are separate.** `facts` are measurements, each naming its source: an alert, a metric query link, a log search by request ID, or an evaluation report. `hypotheses` are inferences with a confidence and a reason. `uncertainty` says what the evidence cannot show.
- **Unknown stays unknown.** When no rule explains a breach, `hypotheses` is empty and `uncertainty` starts with "Cause unknown".
- **Correlation.** Failed requests are matched by request ID and to the last 5 minutes. A request that logged a model error and returned 5xx gives a high-confidence link; failures that only share the time window give medium.
- **What was not checked is stated.** If Prometheus cannot be read, `not_checked` lists `alerts` and `metrics`, so an empty `incidents` list is not read as healthy. If PostgreSQL is down, reports still come back with `persisted: false` and `evaluation_runs` under `not_checked`.
- **Nothing runs.** `suggested_actions` is fixed text for a person. No code path executes a command or changes the system, and no model output reaches a report.

A repeat of an open incident updates it and adds to `occurrences`. A person closes it with `resolve`; if the problem returns, detection opens a new one.

```powershell
$d = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/incidents/detect
$d.not_checked; $d.incidents | Select-Object type, severity, occurrences
Invoke-RestMethod "http://127.0.0.1:8080/api/v1/incidents/$($d.incidents[0].id)/report"     # Markdown
Invoke-RestMethod -Method Post "http://127.0.0.1:8080/api/v1/incidents/$($d.incidents[0].id)/resolve"
```

## Reproduce an incident

Each runbook has a live drill. To replay a recorded snapshot without breaking anything, name a file from `data/incidents/`; these incidents are stored with `source: "simulated"` and never merge with live ones:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/incidents/simulate `
  -ContentType 'application/json' -Body '{"scenario": "unexplained_error_rate"}'
```

Scenarios: `model_timeout`, `model_unavailable`, `increased_latency`, `elevated_api_error_rate`, `retrieval_quality_regression`, `unhealthy_dependency`, and `unexplained_error_rate` (a breach with no explanation, which reports the cause as unknown).

All six incident types were reproduced live against this stack. For example, with `AEGIS_OLLAMA_URL` pointed at a closed port and 33 failed chats, detection reported `model_unavailable` and `elevated_api_error_rate`, named 33 requests by ID, and linked them to the outage with high confidence. A degraded evaluation run reported `top_k 4 -> 1, min_score 0.7 -> 0.0` against the last passing run.

## Limitations

- Detection runs when called; there is no background polling or notification.
- `AegisBackendDown` cannot produce an incident, because the backend is what runs detection. Prometheus still fires the alert.
- Alerts look back 5 minutes, so a report can describe a problem that has already recovered. Detection does not probe Ollama, because a failed probe would itself count as a model failure.
- Failed-request events are kept in memory, the latest 500 per process. After a restart a report has no request IDs and confidence drops to medium.
- Latency hypotheses compare percentiles of separate histograms, which is an approximation. Retrieval p95 reads at most 5 s, the top histogram bucket.
- Comparing evaluation runs covers recorded settings only. A changed document index does not show up.
