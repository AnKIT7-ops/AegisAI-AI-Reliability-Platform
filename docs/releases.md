# Simulated releases

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

One release is active at a time, and baselines and rollback targets must share its source. The database enforces the rules that matter most: a `CHECK` ties status to the gate result, a partial unique index allows one active release, and on PostgreSQL triggers make the snapshot and the canary result immutable and the event history append-only. There is no delete endpoint.

With one shared password, an event's `actor` shows how the action was authorized, not which person took it.

```powershell
$run = (Invoke-RestMethod "http://127.0.0.1:8080/api/v1/evaluations?limit=1").runs[0]
$body = @{ version = "1.0.0"; evaluation_run_id = $run.id } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/releases -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post "http://127.0.0.1:8080/api/v1/releases/$($r.id)/canary"
Invoke-RestMethod -Method Post "http://127.0.0.1:8080/api/v1/releases/$($r.id)/promote"
```

Release records are permanent, so try this on data you are happy to keep.
