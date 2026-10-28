"""Prometheus metrics. Every label has a small, fixed set of values."""

from prometheus_client import Counter, Gauge, Histogram

# Model calls can take tens of seconds; HTTP buckets reach the generation timeout.
SLOW_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

HTTP_REQUESTS = Counter(
    "aegis_http_requests_total",
    "HTTP requests by route template and status code.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "aegis_http_request_duration_seconds",
    "HTTP request duration, including model calls.",
    ["method", "route"],
    buckets=SLOW_BUCKETS,
)
MODEL_REQUESTS = Counter(
    "aegis_model_requests_total",
    "Calls to the model provider by operation and outcome.",
    ["operation", "outcome"],  # outcome: ok or a ModelUnavailable reason
)
MODEL_DURATION = Histogram(
    "aegis_model_request_duration_seconds",
    "Model provider call duration.",
    ["operation"],
    buckets=SLOW_BUCKETS,
)
MODEL_TOKENS = Counter(
    "aegis_model_tokens_total",
    "Tokens reported by the chat model.",
    ["kind"],  # prompt, completion
)
RETRIEVAL_DURATION = Histogram(
    "aegis_retrieval_duration_seconds",
    "Query embedding plus vector search.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
CHAT_ANSWERS = Counter(
    "aegis_chat_answers_total",
    "Chat answers by outcome.",
    ["outcome"],  # answered, refused_low_score, refused_by_model
)
DOCUMENTS_INGESTED = Counter(
    "aegis_documents_ingested_total",
    "Ingest requests that stored a document or found it already present.",
    ["result"],  # created, duplicate
)
EVALUATION_RUNS = Counter(
    "aegis_evaluation_runs_total",
    "Finished evaluation runs.",
    ["status"],  # done, failed
)
EVALUATION_LAST_METRIC = Gauge(
    "aegis_evaluation_last_metric",
    "Metrics of the most recent finished evaluation run, whatever its label.",
    ["metric"],
)
EVALUATION_LAST_GATE = Gauge(
    "aegis_evaluation_last_gate_passed",
    "Most recent finished run: 1 gate passed, 0 failed, NaN no thresholds file.",
)
# Pre-create every outcome so the first failure is a 0 -> 1 step increase() sees.
for _op in ("chat", "embed", "tags"):
    for _outcome in (
        "ok",
        "timeout",
        "unreachable",
        "model_missing",
        "http_error",
        "bad_response",
        "injected",
    ):
        MODEL_REQUESTS.labels(_op, _outcome)

# A gauge starts at 0, which would read as a failed gate after every restart.
EVALUATION_LAST_GATE.set(float("nan"))
