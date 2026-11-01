# Models and configuration


Ollama runs on the host and stays bound to `127.0.0.1:11434`; the backend container reaches it at `host.docker.internal:11434` (Docker Desktop). Do not bind Ollama to `0.0.0.0`.

```powershell
ollama pull qwen3.5:4b         # chat, ~3.3 GB
ollama pull nomic-embed-text   # embeddings, ~270 MB
```

| Role | Model | Why |
|---|---|---|
| Chat | `qwen3.5:4b` (4.2B, Q4_K_M) | Fits a 6 GB GPU with headroom; good instruction following for its size. Called with thinking disabled and temperature 0. |
| Embeddings | `nomic-embed-text` (768-dim) | Small and fast on GPU (32 chunks in ~0.5 s); served by the same Ollama, so no PyTorch dependency in the backend. Uses `search_document:` / `search_query:` prefixes. |

Settings are environment variables with the `AEGIS_` prefix (see `backend/app/core/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `AEGIS_LLM_PROVIDER` | `ollama` | `fake` runs without any model (tests, CI) |
| `AEGIS_OLLAMA_URL` | `http://127.0.0.1:11434` (compose: `http://host.docker.internal:11434`) | Ollama base URL |
| `AEGIS_CHAT_MODEL` / `AEGIS_EMBED_MODEL` | `qwen3.5:4b` / `nomic-embed-text` | Model names |
| `AEGIS_TOP_K` | `4` | Chunks retrieved per question (1–10) |
| `AEGIS_MIN_SCORE` | `0.70` | Cosine score below which a chunk counts as irrelevant |
| `AEGIS_CONNECT_TIMEOUT_S` / `AEGIS_GENERATE_TIMEOUT_S` | `3` / `120` | Ollama timeouts |
| `AEGIS_CHUNK_SIZE` / `AEGIS_CHUNK_OVERLAP` | `800` / `100` | Chunking, in characters |
| `AEGIS_MAX_UPLOAD_BYTES` | `1000000` | Upload limit |
| `AEGIS_FAULT_DELAY_MS` / `AEGIS_FAULT_ERROR_RATE` | `0` / `0` | Monitoring drills only: a delay before every Ollama call (0 to 60000 ms) and the share of calls that fail as `injected` (0 to 1). The backend logs a warning at startup while either is set. See [Runbooks](observability.md#runbooks) |
| `AEGIS_PROMETHEUS_URL` | `http://127.0.0.1:9090` (compose: `http://prometheus:9090`) | Where incident detection reads alerts and metrics |
| `AEGIS_PROMETHEUS_PUBLIC_URL` | `http://127.0.0.1:9090` (compose: `http://localhost:8080/prometheus`) | Base of the links in incident reports; must open in a browser |
| `AEGIS_GRAFANA_PUBLIC_URL` | `http://127.0.0.1:3000` (compose: `http://localhost:8080/grafana/`) | Grafana link in the telemetry summary |
| `AEGIS_DEMO_DIR` | `data/demo` | Demo fixtures |
| `AEGIS_AUTH_PASSWORD_HASH` | empty | Turns on [sign-in](security.md#sign-in). A malformed value stops the backend at startup |
| `AEGIS_AUTH_COOKIE_SECURE` | `false` | Marks the session cookie `Secure`; needs HTTPS in front |
| `AEGIS_AUTH_SESSION_HOURS` | `8` | Absolute session lifetime |
| `FRONTEND_PORT` / `FRONTEND_BIND` | `8080` / `127.0.0.1` | Compose only: where nginx is published |
| `AEGIS_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`; `debug` adds health-check requests |
| `AEGIS_ALLOWED_HOSTS` | `["localhost","127.0.0.1","backend"]` | Host headers the API answers (JSON list); others get 400 |
| `AEGIS_ALLOWED_ORIGINS` | `["http://localhost:8080","http://127.0.0.1:8080","http://localhost:5173","http://127.0.0.1:5173"]` | Browser origins allowed to send POST and other non-GET requests (JSON list); others get 403. The API's own origin (the `/docs` page) and requests without an `Origin` header (curl, scripts) are allowed. Compose sets it from `FRONTEND_PORT` |

Changing the embedding model requires a fresh index: the index records which model built it and refuses to load otherwise. Delete it with `docker compose down; docker volume rm aegis-ai_ragindex` (WARNING: removes all ingested documents), start the stack and re-run the ingest script.
