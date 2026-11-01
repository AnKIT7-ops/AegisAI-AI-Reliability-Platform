# Benchmark 2026-11-01

Commit `99ee796`. Took 255 s. Raw values are in `results.json`.

| Setting | Value |
|---|---|
| Hardware | Intel Core i7-13700HX, 16 GB RAM, NVIDIA RTX 4050 Laptop GPU 6 GB |
| Operating system | Windows-11-10.0.26300-SP0 |
| Docker | 29.8.1 |
| Ollama | 0.40.12 |
| Chat model | `qwen3.5:4b` |
| Embedding model | `nomic-embed-text` |
| Prompt version | 2 |
| Dataset | v1, checksum `7d479629a3cd3993` |
| Retrieval | top_k 4, min_score 0.7 |
| Documents ingested | 15 |

## Latency

Client side, through nginx. Percentiles are nearest rank over successful requests. Wrong means HTTP 200 with the wrong kind of reply: a refusal in the Answered rows, or an answer in the Refused row. Those requests are left out of the latency figures.

| Scenario | Concurrency | Requests | OK | Wrong | Failed | 429 | Requests/s | p50 ms | p95 ms | Max ms |
|---|---|---|---|---|---|---|---|---|---|---|
| Read document list | 1 | 200 | 200 | 0 | 0 | 0 | 290.76 | 2 | 5 | 26 |
| Read document list | 8 | 200 | 200 | 0 | 0 | 0 | 678.98 | 11 | 20 | 36 |
| Refused, no chat model | 1 | 40 | 40 | 0 | 0 | 0 | 1.52 | 59 | 79 | 90 |
| Answered | 1 | 40 | 38 | 2 | 0 | 0 | 0.78 | 1022 | 2898 | 2995 |
| Answered | 2 | 40 | 38 | 2 | 0 | 0 | 0.78 | 2158 | 4399 | 4962 |
| Answered | 4 | 40 | 38 | 2 | 0 | 0 | 0.83 | 4175 | 6948 | 7018 |

## Evaluation

Status `done`, quality gate passed.

| Metric | Value |
|---|---|
| `examples` | 69 |
| `errors` | 0 |
| `failure_rate` | 0 |
| `recall_at_k` | 0.9804 |
| `mrr` | 0.9575 |
| `answer_correctness` | 0.8235 |
| `citation_correctness` | 1 |
| `refusal_accuracy` | 0.9508 |
| `false_refusal_rate` | 0.0588 |
| `correct_refusal_rate` | 1 |
| `injection_resistance` | 0.625 |
| `latency_ms_p50` | 931 |
| `latency_ms_p95` | 2745 |
| `prompt_tokens_total` | 42384 |
| `completion_tokens_total` | 2544 |

## Peak resource use

Highest of 34 samples (0 skipped), one about every 5 s, so short spikes can be missed. GPU memory is the whole GPU: 4990 MiB.

| Service | CPU % | Memory MiB |
|---|---|---|
| backend | 81.2 | 144.8 |
| frontend | 1.3 | 25.9 |
| grafana | 109.2 | 390.5 |
| postgres | 4.8 | 75.7 |
| prometheus | 14.0 | 35.8 |
