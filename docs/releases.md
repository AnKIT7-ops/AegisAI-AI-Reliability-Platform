# Demo data and simulated releases

## Demo data

The demo works with no model installed:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/demo/load
Invoke-RestMethod http://127.0.0.1:8080/api/v1/demo
```

`data/demo/` holds three real evaluation runs, exported from a run of this project on the sample documents. No number in it is invented. Loading adds:

| Record | Detail |
|---|---|
| Evaluation runs | Three, with every per-example answer, marked `source: demo`. Two pass the quality gate and one fails it. |
| Releases | `demo-1` superseded, `demo-2` active, `demo-3` rejected, with their history. |
| Incident | `model_timeout`, diagnosed from `data/incidents/model_timeout.json`, marked `simulated`. |

The documents and recorded answers are shown from `GET /api/v1/demo`; nothing is written to the document index. Questions you ask in the chat still need Ollama.

Loading is safe to repeat. It inserts only what is absent and never updates or deletes, so anything you did to a demo record stays. There is no way to remove demo records, and they accept release actions, so a rollback you try is kept. Demo records never mix with live ones: incident detection ignores demo runs, and baselines and rollback targets must share a source.

The quality gate is applied at load time. If the dataset or thresholds change so that the runs no longer tell the same story, loading returns `409 demo_stale` and writes nothing. Regenerate the fixtures with the stack running and sign-in off:

```powershell
python scripts/export_demo.py
```

## Simulated releases

A release is a frozen record of one completed evaluation run: dataset, prompt, models, retrieval settings and metrics. It records a decision. **It does not change live settings**, and every response says `"simulated": true`.

```
candidate --canary, gate passed--> canary_passed --promote--> active --rollback--> rolled_back
    |                                                           ^
    | canary, gate failed                                       | target of a rollback
    v                                                           |
 rejected                                                   superseded
```

| Rule | Detail |
|---|---|
| Creating | The run must be `done`. Otherwise `409 evaluation_not_completed`, and no record exists. A completed run with a failing gate still becomes a candidate, so its failures stay on record. |
| Canary | The candidate's own run is the canary evaluation; no new run starts. The evaluation quality gate alone decides pass or fail. A failure gives `rejected`, which is final. With no thresholds file: `409 gate_unavailable`. |
| Comparison | With the release you name, or the active release. Status is `comparable`, `no_baseline` or `dataset_changed`, and deltas appear only when comparable. Regressions inside the gate limits are shown and do not block. |
| Promotion | Only from `canary_passed`, and the gate is checked again first: `409 gate_failed` otherwise. The previous active release becomes `superseded`. A refused promotion changes nothing and writes a `promotion_refused` event. |
| Rollback | Only the active release, to a named `superseded` release, with a reason. No new release is created. |

Each source (`live`, `demo`) has one active release, and baselines and rollback targets must share the source. The database enforces the rules that matter most: a `CHECK` ties status to the gate result, a partial unique index allows one active release, and on PostgreSQL triggers make the snapshot and the canary result immutable and the event history append-only. There is no delete endpoint.

With one shared password, an event's `actor` shows how the action was authorized, not which person took it.

```powershell
$run = (Invoke-RestMethod "http://127.0.0.1:8080/api/v1/evaluations?limit=1").runs[0]
$body = @{ version = "1.0.0"; evaluation_run_id = $run.id } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/releases -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post "http://127.0.0.1:8080/api/v1/releases/$($r.id)/canary"
Invoke-RestMethod -Method Post "http://127.0.0.1:8080/api/v1/releases/$($r.id)/promote"
```

Release records are permanent, so try this on data you are happy to keep.
