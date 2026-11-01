# Benchmarks

One run on one laptop: Intel Core i7-13700HX, 16 GB RAM, NVIDIA RTX 4050 Laptop GPU (6 GB), Windows 11, Docker 29.8, Ollama 0.40.12, `qwen3.5:4b`, `nomic-embed-text`, top_k 4, min_score 0.70, 15 documents. The full report, raw latencies and settings are in [docs/benchmarks/2026-11-01](benchmarks/2026-11-01/report.md). The report names commit `99ee796`, the branch commit it ran on. The same script is `c715322` on `main`.

Latency is measured at the client, through nginx. Percentiles are nearest rank over successful requests. **Wrong** counts an HTTP 200 with the wrong kind of reply: here, an answerable question that came back with no sources, which is how a refusal looks. Those requests are left out of the latency figures. The check reads `sources` only, so the saved results do not show the reply text.

| Scenario | Concurrency | Requests | Wrong | Failed | p50 | p95 | Requests/s |
|---|---|---|---|---|---|---|---|
| Read the document list | 1 | 200 | 0 | 0 | 2.5 ms | 5 ms | 291 |
| Read the document list | 8 | 200 | 0 | 0 | 11 ms | 20 ms | 679 |
| Out-of-scope question, no sources returned | 1 | 40 | 0 | 0 | 59 ms | 79 ms | 1.52 (paced) |
| Answered question | 1 | 40 | 2 | 0 | 1.02 s | 2.90 s | 0.78 |
| Answered question | 2 | 40 | 2 | 0 | 2.16 s | 4.40 s | 0.78 |
| Answered question | 4 | 40 | 2 | 0 | 4.17 s | 6.95 s | 0.83 |

- Throughput of answered questions stayed about the same as concurrency rose, so more simultaneous users meant longer waits. No request returned an error and none hit the rate limit. The likely cause is one model on one GPU, which this run does not prove.
- The out-of-scope row is paced to stay under the rate limit, so its requests per second is set by the script, not by the system.
- The live evaluation in that run passed its gate with the quality metrics of the [baseline](evaluation.md#baseline) and no failed examples. Its own latency was p50 0.93 s and p95 2.75 s.
- Peak memory: backend 145 MiB, Grafana 391 MiB, PostgreSQL 76 MiB, Prometheus 36 MiB, nginx (the `frontend` service) 26 MiB. The GPU peaked at 4990 MiB in use across the whole card. Docker and the GPU were sampled about every 5 s, so short spikes can be missed.

These figures describe this machine on this day. Forty chat requests per level is a small sample, and latency on this laptop moves between runs, so read them as rough sizes. They are not a capacity claim.

To reproduce it, with Ollama running and sign-in off (the script took 255 s here, evaluation included):

```powershell
docker compose up -d --wait
.\scripts\ingest_samples.ps1
python scripts/benchmark.py --hardware "<CPU, RAM, GPU>" --out docs/benchmarks/<date>
```

Use `127.0.0.1` rather than `localhost` for any measurement on Windows. nginx is published on IPv4 only, so a client that opens a new connection to `localhost` can wait about 2 s for `::1` to fail first.
