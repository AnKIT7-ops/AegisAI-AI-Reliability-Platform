# Unhealthy dependency

Incident type: `unhealthy_dependency`: PostgreSQL did not answer `SELECT 1` during detection.

## What you see

- `GET /health/ready` returns `503 {"status": "not ready", "checks": {"database": "unavailable"}}`.
- `POST /api/v1/incidents/detect` still answers, with `persisted: false`: the report cannot be saved while the database is down. Save it from the response if you need it.
- Evaluation endpoints return `503 database_unavailable`. Chat and retrieval do not use the database and keep working.

## Find the cause

1. `docker compose ps postgres`: is the container running and healthy?
2. `docker compose logs postgres --tail 50`: look for out-of-memory kills, a full disk or authentication failures.
3. If the container is healthy, compare `DATABASE_URL` with `.env`. The password in `.env` must use letters and digits only, and it only takes effect on a new volume.

Do not delete the `pgdata` volume to recover: that destroys evaluation history and incidents.

## Reproduce it (drill)

```powershell
docker compose stop postgres
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/incidents/detect | ConvertTo-Json -Depth 6
```

Expected: an `unhealthy_dependency` incident, `persisted: false`, and `evaluation_runs` under `not_checked`.

## Clean up

```powershell
docker compose start postgres
```

The incident is not saved, so there is nothing to resolve. A later `detect` shows no database incident.
