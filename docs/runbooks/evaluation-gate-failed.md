# Evaluation gate failed

Alert: `AegisEvaluationGateFailed`: the most recent finished evaluation run did not meet the limits in `data/evaluation/v1.thresholds.json`. Incident type: `retrieval_quality_regression`.

## What you see

- Grafana, *AegisAI overview*: **Last evaluation gate** shows FAIL; **Latest evaluation run** shows which metrics dropped.
- `GET /api/v1/evaluations/{id}` returns `"gate": {"passed": false, "failures": [...]}`; the Markdown report lists the failures and every example that did not pass.

## Find the cause

1. Read the failures:

   ```powershell
   $run = (Invoke-RestMethod "http://127.0.0.1:8000/api/v1/evaluations?limit=1").runs[0]
   (Invoke-RestMethod "http://127.0.0.1:8000/api/v1/evaluations/$($run.id)").gate
   ```

2. Match the message:

   | Failure | Meaning | Next step |
   |---|---|---|
   | `thresholds were set on dataset checksum ...` | The dataset changed since the baseline | Re-measure the baseline and update the thresholds file |
   | `the thresholds file is invalid` | The thresholds file could not be read or parsed | Fix the JSON; the gate fails closed until then |
   | `failure_rate: ... > 0` | Model calls failed during the run | See [error-rate spike](error-rate-spike.md) |
   | `latency_ms_p95: ... > 5000` | The run was slow | See [latency spike](latency-spike.md) |
   | A quality metric below its limit | Answers, retrieval or refusals got worse | Compare with the last passing run (below) |

3. Compare with the last passing run and read the examples that changed:

   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/evaluations/$($run.id)/report?baseline=<passing run id>"
   ```

   Check what changed between the runs: `chat_model`, `embed_model`, `prompt_version`, `top_k`, `min_score` and `config` are stored with every run.

## Reproduce it (drill)

A deliberately degraded configuration fails the gate:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/evaluations/run `
  -ContentType 'application/json' -Body '{"label": "degraded", "top_k": 1, "min_score": 0.0}'
```

Expected after about a minute: **Last evaluation gate** shows FAIL and `AegisEvaluationGateFailed` fires. A normal run (`{"label": "baseline"}`) clears it.
