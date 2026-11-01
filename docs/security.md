# Security


| Area | What is in place |
|---|---|
| Network | Only nginx is published, on 127.0.0.1 by default. Exposing it needs [sign-in and a TLS terminator](#exposing-beyond-localhost); the backend refuses to start otherwise. |
| Requests | Bodies over 2 MB are refused by nginx, uploads over 1 MB and questions over 2000 characters by the API. `Host` and `Origin` are checked. |
| Rate limits | Chat, upload, starting an evaluation, incident detection, simulation and demo loading share a limit of 2 requests per second per client address with a burst of 40, then `429 rate_limited` with `Retry-After: 1`. Failed sign-ins have their own limit. Behind Docker Desktop all clients can appear as one address, so the limit can act as a global one. |
| Sessions | With sign-in on: `HttpOnly`, `SameSite=Strict` cookie, a CSRF token on every change, and revocation on sign-out. Passwords are stored as scrypt hashes. |
| Containers | The backend and nginx run as non-root users. All services run with `no-new-privileges`. All except PostgreSQL drop every Linux capability. The backend, nginx and Prometheus have a read-only root filesystem. Every service has a memory limit. |
| Untrusted content | Model output and document text render as plain text. The browser allows only same-origin scripts, styles and requests. Retrieved text cannot run anything, because the model has no tools. Ids from the URL must be UUIDs before they reach an API path. |
| Errors and logs | Errors carry a code, a message and a request ID, never a stack trace. Questions, answers and document text are not logged, and secrets are masked. |
| Dependencies | Versions are locked. CI scans npm packages and both images. Actions and the scanner are pinned to digests. |

PostgreSQL keeps its capabilities because it starts as root to set up its data directory. Grafana keeps a writable filesystem for its own database.

## Sign-in

Sign-in is off by default, for use on your own machine. To require a shared password:

```powershell
docker compose run --rm --no-deps backend python -m app.core.auth    # asks twice, prints the hash
notepad .env                                                          # AEGIS_AUTH_PASSWORD_HASH=<the hash>
docker compose up -d --wait
```

With a hash set, every application route needs a session, including `/prometheus/` and `/grafana/`. Sign-in goes through `POST /api/v1/auth/login` with `{"password": "..."}`, and the response carries a CSRF token that non-GET requests must send as `X-CSRF-Token`. Sessions last 8 hours and are held in memory, so a backend restart signs everyone out. Five failed attempts from one address in 15 minutes return 429; behind Docker Desktop all clients can appear as one address, so that limit can act as a global one.

Use `localhost` rather than `127.0.0.1` in the browser, because the session cookie belongs to the host name and report links use `localhost`. `scripts/ingest_samples.ps1` and the PowerShell examples in these docs assume sign-in is off.

### Exposing beyond localhost

Set `FRONTEND_BIND` to the address to publish on. The backend then refuses to start unless `AEGIS_AUTH_PASSWORD_HASH` is set and `AEGIS_AUTH_COOKIE_SECURE=true`. Also add the host name to `AEGIS_ALLOWED_HOSTS` and the origin to `AEGIS_ALLOWED_ORIGINS` in `.env`, or the request guards reject it.

AegisAI does not terminate TLS. Put a TLS terminator in front, because the password and session cookie would otherwise cross the network in clear text. Links in incident reports and Grafana's root URL keep `localhost`, so set `AEGIS_PROMETHEUS_PUBLIC_URL` for correct report links.
