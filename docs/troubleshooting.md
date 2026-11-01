# Troubleshooting


| Symptom | Likely cause | What to do |
|---|---|---|
| `failed to connect to the docker API` | Docker Desktop is not running | Start it, then run the command again |
| `up` stops with `set in .env` | `.env` is missing or a required value is empty | `Copy-Item .env.example .env`, then set the passwords |
| `port is already allocated` | Something else uses port 8080 | Set `FRONTEND_PORT` in `.env` |
| The backend never becomes healthy | Bad settings, such as symbols in `POSTGRES_PASSWORD`, a malformed password hash, or a non-loopback `FRONTEND_BIND` without sign-in | `docker compose logs backend` |
| `503 model_unavailable` | Ollama is down, slow or missing a model | `Invoke-RestMethod http://127.0.0.1:8080/api/v1/models` shows which, then see [Models](configuration.md) |
| The first answer takes a minute | The model is loading | Wait. Later answers are much faster |
| Telemetry reads "No traffic" | Prometheus has not scraped twice yet, or no requests were made | Wait about 30 seconds and refresh |
| `400` from every API call | The `Host` header is not allowed | Add the host name to `AEGIS_ALLOWED_HOSTS` |
| `403` on a change | The browser origin is not allowed, or the CSRF token is missing | Add the origin to `AEGIS_ALLOWED_ORIGINS`, or sign in again |
| `429 rate_limited` | Too many costly requests in a short time | Wait and retry. See [Security](security.md) |
| `409 evaluation_running` | One run is already going | Wait for it to finish |
| `409 demo_stale` | The dataset or limits changed under the fixtures | See [Demo data](releases.md#demo-data) |
| The index refuses to load | The embedding model changed | See [Models](configuration.md) for rebuilding it |
| Sign-in does not stick | The browser is on `127.0.0.1` | Use `localhost` in the browser |
| Scripts take 2 s per request on Windows | They connect to `localhost` | Use `127.0.0.1` |

## Known limitations

This is a single-machine system for evaluating and operating a local RAG application. It is not a production deployment.

- **One process on one machine.** Sessions live in memory in one uvicorn process, so a restart signs everyone out and adding workers would break sign-in. The document index is also held in memory.
- **One shared password.** There are no user accounts, and an audit event shows how an action was authorized, not who took it.
- **No TLS.** Exposing the stack needs a TLS terminator in front. See [Exposing beyond localhost](security.md#exposing-beyond-localhost).
- **Releases are simulated.** A promotion or rollback records a decision and changes no live setting.
- **Model capacity.** Answered throughput stayed flat as concurrency rose in the [benchmark](benchmarks.md), so waits grow with the number of users. The rate limit does not queue or shed by cost.
- **Rate limits are per address.** Behind Docker Desktop all clients can share one address, so one client can use up the limit for everyone.
- **Evaluation runs.** Two requests at the same moment can both start a run, because the check and the insert are separate steps. This takes simultaneous clicks in practice.
- **Detection runs when called.** There is no background polling, notification or Alertmanager, and no tracing.
- **Demo records stay.** They cannot be removed without deleting the database volume.
- **Platforms tested.** The real model ran only on Windows 11 with Docker Desktop. Ubuntu is covered by CI with fake model providers.
- **Accessibility.** Browser tests cover the skip link, focus after a route change and 360 px layouts. There has been no screen reader pass.
- **The `e2e` CI job is not a required check yet.** One test failed once locally, its error was not kept, and the cause is unknown.

More specific limits are listed with their features: [answers and retrieval](api.md#limitations), [evaluation measurements](evaluation.md#limitations-of-the-measurements), [the CI gate](ci-cd.md#evaluation-gate-in-ci) and [incident detection](incidents.md#limitations).
