# CI/CD

`.github/workflows/ci.yml` runs on every pull request and every push to `main`. It needs no secrets, paid services or model downloads, and it pushes nothing: images are built and scanned, then discarded.

| Job | Stage | Fails when |
|---|---|---|
| `backend` | Lint and format (`ruff`) | A rule is broken or a file is not formatted |
| | Type check (`mypy` strict) | App, test or migration code has a type error |
| | Unit, API integration and evaluation tests (`pytest`, fake model providers) | A test fails |
| | Migrations on PostgreSQL 18 | `alembic upgrade head`, `check` or `downgrade base` fails, or the models and migrations differ |
| | Evaluation quality gate | A metric misses its limit (below). The report is in the run summary and the `evaluation-gate-report` artifact |
| `frontend` | Lint (`oxlint`), unit tests (`vitest`), type check and build | Lint, a test, `tsc` or the Vite build fails |
| | Dependency scan (`npm audit`) | A runtime dependency has a high or critical advisory |
| `e2e` | Browser tests (Playwright) against the full Compose stack, with sign-in on and no model | A journey fails. Traces are in the `e2e-traces` artifact |
| `monitoring` | Compose file, Prometheus configuration, alert rule tests (`promtool`) | A file is invalid or an alert test fails |
| `images` | Build the backend and frontend images | A Dockerfile fails to build |
| | Vulnerability scan (Trivy) | An image has a high or critical vulnerability with a released fix |

uv, npm and Docker layer caches are keyed on their lockfiles and Dockerfiles. Actions are pinned to commit SHAs and the Trivy image to a digest, because tags can be moved. The workflow token is read-only.

`main` requires five checks (`backend`, `frontend`, `monitoring`, `images (backend)`, `images (frontend)`) to pass, for administrators too, so changes land through pull requests. `e2e` runs on every pull request and is not required yet.

## Evaluation gate in CI

CI has no GPU and downloads no model, so the gate runs the full `v1` dataset over the sample corpus with the fake providers: a hashed bag-of-words embedder and a chat model that echoes the top source. Limits are in `data/evaluation/v1.ci-gate.json`, about one example below the measured run.

| Metric | Fake-provider run | Limit |
|---|---|---|
| `recall_at_k` | 0.92 | ≥ 0.90 |
| `mrr` | 0.76 | ≥ 0.74 |
| `answer_correctness` | 0.14 | ≥ 0.11 |
| `citation_correctness` | 0.67 | ≥ 0.60 |
| `refusal_accuracy` | 0.87 | ≥ 0.85 |
| `false_refusal_rate` | 0 | ≤ 0.02 |
| `correct_refusal_rate` | 0.20 | ≥ 0.10 |
| `injection_resistance` | 1.00 | ≥ 0.85 |
| `failure_rate` | 0 | ≤ 0 |

These numbers describe the pipeline, not the model, and are not comparable with the [baseline](evaluation.md#baseline). The gate catches changes to chunking, retrieval, the default `top_k`, refusals, citation handling and the dataset; editing the dataset fails the gate until the limits are measured again. It cannot catch worse model answers, a changed prompt's effect on a real model, or a changed `AEGIS_MIN_SCORE` default, because the fake embedder needs its own cut-off. Those need the live evaluation in [Run an evaluation](evaluation.md#run-an-evaluation-powershell).

A check of the gate on a pull request that lowered the default `top_k` from 4 to 1: every unit and API integration test still passed, the gate failed with `recall_at_k` 0.67 < 0.90 and `mrr` 0.67 < 0.74, and GitHub blocked the merge ([run](https://github.com/AnKIT7-ops/AegisAI-AI-Reliability-Platform/actions/runs/41184628189)).

Run it locally (PowerShell):

```powershell
cd backend
uv run python -m app.services.evaluation.ci_gate --out ci-report      # exit 0, gate passes
uv run python -m app.services.evaluation.ci_gate --top-k 1 --min-score 0   # degraded, exit 1
```

## Versions and releases

- Releases are annotated Git tags `vMAJOR.MINOR.PATCH` on a `main` commit with a green CI run. Tag pushes do not run CI again, because the commit has already passed. No images are published.
- MAJOR changes break the API, configuration or database schema; MINOR adds behaviour; PATCH fixes it. Before 1.0.0, a MINOR release may also break those. `version` in `backend/pyproject.toml` and `frontend/package.json` is set to the tag.
- Before tagging, run the live evaluation on that commit; the quality gate must pass. The release notes record the run ID, models, prompt version and dataset version with checksum.
- Quality inputs have their own versions: `PROMPT_VERSION` in `backend/app/services/rag/answer.py` and the dataset file name plus checksum. Limit files are pinned to a checksum, so a dataset edit requires a new measurement.
