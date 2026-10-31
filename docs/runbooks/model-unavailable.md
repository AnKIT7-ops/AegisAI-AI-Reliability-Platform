# Model unavailable

Incident type: `model_unavailable`. Alert: `AegisModelFailures` with any outcome except `timeout`.

## What you see

- Clients get `503 {"error": {"code": "model_unavailable", ...}}` straight away.
- `GET /api/v1/models` shows `reachable: false` or `installed: false`. It still returns 200.

## Find the cause

| Outcome | Meaning | Usual fix |
|---|---|---|
| `unreachable` | Ollama is not running or not listening on 127.0.0.1:11434 | Start Ollama; `curl.exe http://127.0.0.1:11434/api/version` |
| `model_missing` | The configured model is not pulled | `ollama list`; pull the model named in `GET /api/v1/models` |
| `http_error`, `bad_response` | Ollama answered with an error or an unexpected body | Read the Ollama logs; restart Ollama |
| `injected` | Fault injection is on | Set `AEGIS_FAULT_ERROR_RATE=0` |

Do not bind Ollama to `0.0.0.0` to work around a connection problem.

## Reproduce it (drill)

A port nothing listens on gives `unreachable`, with Ollama running or not.

```powershell
$env:AEGIS_OLLAMA_URL = "http://127.0.0.1:9"
docker compose up -d --wait backend
1..3 | ForEach-Object {
  try {
    Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/chat -ContentType 'application/json' `
      -Body '{"question": "How long does Prometheus keep data by default?"}' | Out-Null
  } catch { }                              # the 503s are the point
}
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/incidents/detect | ConvertTo-Json -Depth 6
```

`AegisModelFailures` fires on the next evaluation of the rule; run `detect` once it shows at http://127.0.0.1:9090/alerts.

## Clean up

```powershell
Remove-Item Env:AEGIS_OLLAMA_URL
docker compose up -d --wait backend
```
