# Latency spike

Alert: `AegisChatLatencyHigh`: the 95th percentile of `/api/v1/chat` is above 10 s for 2 minutes. The measured baseline p95 is about 2.8 s on an RTX 4050 Laptop GPU (README, *Evaluation*). Incident type: `increased_latency`.

## What you see

- Grafana, *AegisAI overview*: **Chat p95 latency** turns orange (above 5 s) or red (above 10 s); **Request latency p50 / p95** shows which route is slow.
- **Model call p95 and retrieval p95** shows where the time goes: a slow `model chat` line means generation; a slow `retrieval` line means embedding or vector search.

## Find the cause

1. Is it the model? Compare chat-call time with request time:

   ```promql
   histogram_quantile(0.95, sum by (le, operation) (rate(aegis_model_request_duration_seconds_bucket[5m])))
   histogram_quantile(0.95, sum by (le) (rate(aegis_retrieval_duration_seconds_bucket[5m])))
   ```

2. Common causes:

   | Cause | How it shows | Fix |
   |---|---|---|
   | Model loading after Ollama started or unloaded it | One slow request (38 to 79 s measured), then normal | Warm it with one request; Ollama keeps a model loaded for about 5 minutes after use |
   | Model running on CPU instead of GPU | Every chat call slow | `ollama ps` shows `PROCESSOR`; free GPU memory or use a smaller model |
   | Large prompts | **Tokens per second** shows high prompt tokens | Lower `top_k` or `AEGIS_CHUNK_SIZE` |
   | Evaluation run in progress | **Evaluation runs** shows activity; chat traffic shares the model | Wait for the run (about 70 s for 69 examples), or run evaluations off-hours |

3. Slow individual requests in the logs (`duration_ms` is end-to-end):

   ```powershell
   docker compose logs --no-log-prefix backend | Select-String '"event": "chat"' | Select-Object -Last 10
   ```

## Reproduce it (drill)

Fault injection adds a fixed delay before every Ollama call (embedding and chat, so a chat request is delayed twice).

```powershell
$env:AEGIS_FAULT_DELAY_MS = "6000"         # two calls per chat: about 12 s added
docker compose up -d --wait backend
1..12 | ForEach-Object {
  Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/chat -ContentType 'application/json' `
    -Body '{"question": "How long does Prometheus keep data by default?"}' | Out-Null
  Start-Sleep 5
}
```

Expected: **Chat p95 latency** rises above 10 s and, after 2 minutes, `AegisChatLatencyHigh` fires (http://localhost:8080/prometheus/alerts). The model-call panel shows the added 6 s per call.

## Clean up

```powershell
Remove-Item Env:AEGIS_FAULT_DELAY_MS
docker compose up -d --wait backend
```

The alert resolves once the slow requests leave the 5-minute window.
