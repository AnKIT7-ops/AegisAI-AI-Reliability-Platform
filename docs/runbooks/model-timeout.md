# Model timeout

Incident type: `model_timeout`. Alert: `AegisModelFailures` with outcome `timeout`: an Ollama call passed `AEGIS_GENERATE_TIMEOUT_S` (default 120 s) or `AEGIS_CONNECT_TIMEOUT_S` (default 3 s).

## What you see

- Clients get `503 {"error": {"code": "model_unavailable", "message": "Ollama timed out"}}` after a long wait.
- Grafana, *AegisAI overview*: **Model calls by outcome** shows `timeout`.

## Find the cause

1. Confirm the outcome:

   ```promql
   sum by (operation, outcome) (increase(aegis_model_requests_total{outcome="timeout"}[15m]))
   ```

2. Check the model. `ollama ps` shows whether it is loaded and whether it is on the GPU. The first request after Ollama starts took 38 to 79 s here.
3. Check the GPU. Another program holding its memory pushes the model to the CPU, which is far slower.
4. Only if timeouts continue on a warm model, raise `AEGIS_GENERATE_TIMEOUT_S`.

## Reproduce it (drill)

An address that never answers makes every call time out. It needs no Ollama.

```powershell
$env:AEGIS_OLLAMA_URL = "http://10.255.255.1:11434"
docker compose up -d --wait backend
1..3 | ForEach-Object {
  try {
    Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/chat -ContentType 'application/json' `
      -Body '{"question": "How long does Prometheus keep data by default?"}' | Out-Null
  } catch { }                              # the 503s are the point
}
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/incidents/detect | ConvertTo-Json -Depth 6
```

Run `detect` after `AegisModelFailures` shows as firing at http://localhost:8080/prometheus/alerts.

## Clean up

```powershell
Remove-Item Env:AEGIS_OLLAMA_URL
docker compose up -d --wait backend
```
