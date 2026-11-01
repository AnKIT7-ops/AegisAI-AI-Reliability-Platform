# Evaluation

The evaluation harness runs a versioned question set through the same retrieval and answering code as `/api/v1/chat`, scores every answer, stores the run in PostgreSQL and writes a report.

## Dataset

`data/evaluation/v1.jsonl` has 69 examples, one JSON object per line:

| Category | Count | Expected behaviour |
|---|---|---|
| `answerable` | 51 | Answer from the documents, with key facts and a citation. Covers all 15 sample documents |
| `unanswerable` | 10 | Refuse. Includes near misses in the same domains (CronJob syntax, Loki LogQL) |
| `injection` | 8 | Resist an instruction written into the question (extra words, prompt disclosure, a fake `<context>` block, a planted link) |

Each example lists its expected source files, the key facts the answer must contain (with accepted alternative phrasings) and, for injection cases, phrases the answer must not contain. Every key fact was checked against the source text, and a test fails if a key fact appears in its own question. Each run records the dataset version and a checksum of the file, so runs on an edited dataset are never compared silently.

## Run an evaluation (PowerShell)

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/v1/evaluations/run `
  -ContentType 'application/json' -Body '{"label": "baseline"}'
do { Start-Sleep 10; $r = Invoke-RestMethod "http://127.0.0.1:8080/api/v1/evaluations/$($run.id)" } `
  while ($r.status -in 'pending', 'running')
$r.metrics; $r.gate
# Overrides for an experiment: '{"label": "degraded", "top_k": 1, "min_score": 0.0}'
Invoke-RestMethod "http://127.0.0.1:8080/api/v1/evaluations/$($run.id)/report"   # Markdown
```

One run executes at a time (`409 evaluation_running` otherwise). A run interrupted by a restart is marked failed on the next start. Each finished run also writes `report.json`, `results.csv` and `report.md` to the `evalruns` volume.

## Metrics

| Metric | Definition |
|---|---|
| `recall_at_k` | Answerable examples whose top-k retrieved chunks include an expected document (before the score cut-off) |
| `mrr` | Mean of 1 / rank of the first expected document in the top k (0 if absent) |
| `answer_correctness` | Answerable examples answered (not refused) with every key fact present; case-insensitive substring match |
| `citation_correctness` | Answered, answerable examples that cite at least one source, cite only numbers that exist, and cite at least one expected document |
| `refusal_accuracy` | Answerable and unanswerable examples where "refused" matches the category |
| `false_refusal_rate` / `correct_refusal_rate` | Answerable examples refused / unanswerable examples refused |
| `injection_resistance` | Injection examples whose answer contains no forbidden phrase |
| `failure_rate` | Examples where the model call failed (timeout, Ollama down) |
| `latency_ms_p50` / `latency_ms_p95` | Nearest-rank percentiles of end-to-end answer latency (embedding, search, generation) |
| `prompt_tokens_total` / `completion_tokens_total` | Token counts reported by Ollama |

## Baseline

Measured on 2026-10-28 through the API: qwen3.5:4b and nomic-embed-text on Ollama 0.40.12, RTX 4050 Laptop GPU (6 GB), dataset checksum `7d479629a3cd3993`, prompt version 1; a prompt version 2 baseline (tighter `<context>` neutralizing) gave identical quality metrics. "Degraded" is a deliberately worse configuration (one retrieved chunk, no score cut-off).

| Metric | Baseline (top_k 4, min_score 0.70) | Degraded (top_k 1, min_score 0) |
|---|---|---|
| recall_at_k | 0.98 | 0.94 |
| mrr | 0.96 | 0.94 |
| answer_correctness | 0.82 (42/51) | 0.61 (31/51) |
| citation_correctness | 1.00 | 1.00 |
| refusal_accuracy | 0.95 | 0.80 |
| false_refusal_rate | 0.06 (3/51) | 0.24 (12/51) |
| correct_refusal_rate | 1.00 (10/10) | 1.00 (10/10) |
| injection_resistance | 0.62 (5/8) | 0.62 (5/8) |
| failure_rate | 0 | 0 |
| latency p50 / p95 | 0.92 s / 2.8 s | 0.58 s / 1.4 s |
| Wall time for 69 examples | 70 s | 50 s |

A second baseline run gave identical quality metrics and token counts (temperature 0); only latency varied (p50 0.95 s, p95 2.8 s). The injections the model followed: appending a requested word ("PWNED"), printing its system prompt on request, and adding a requested link to the answer.

## Quality gate

`data/evaluation/v1.thresholds.json` holds limits set from the baseline, about one to three examples below it (for example `answer_correctness` ≥ 0.75, `refusal_accuracy` ≥ 0.90, `false_refusal_rate` ≤ 0.10, `latency_ms_p95` ≤ 5000 ms). `GET /api/v1/evaluations/{id}` and the Markdown report show pass or fail with the failing metrics. The baseline passes; the degraded configuration fails on recall, answer correctness, refusal accuracy and false refusals.

## Limitations of the measurements

- Key facts and forbidden phrases are literal substring matches. A correct answer in unexpected wording scores as wrong, and a refusal that quotes an injected claim can score as followed; read the failing examples in the report before acting on a number.
- 69 examples is small: one answerable example moves `answer_correctness` by about 0.02, one injection example moves `injection_resistance` by 0.125.
- The examples were written against this corpus by the project; 15 are queued for independent human review (`reviewed: true` marks the ones checked).
- Retrieval metrics count document-level hits, not whether the retrieved chunk contains the answer.
- Latency depends on the GPU and on whether the model is already loaded (the first request after Ollama starts took 38 to 79 s here); warm the model before measuring.
- Only question-embedded injections are measured; documents with planted instructions are not yet part of the dataset.
