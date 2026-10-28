"""Metrics move when the app does. Values are read as before/after deltas."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.api.deps import get_chat_model
from app.core.config import Settings
from app.main import app
from app.services.evaluation.dataset import load_dataset
from app.services.llm.base import Generation, ModelUnavailable
from app.services.llm.ollama import OllamaChat, OllamaClient, OllamaEmbedder
from tests.integration.test_evaluations_api import start

DOC = b"# Probes\n\nA readiness probe decides whether a pod receives traffic.\n"


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def ingest(client: TestClient, data: bytes = DOC) -> None:
    client.post(
        "/api/v1/documents/ingest", files={"file": ("p.md", data, "text/markdown")}
    )


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    # Prometheus in compose scrapes with Host: backend:8000.
    r = client.get("/metrics", headers={"host": "backend:8000"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "aegis_http_requests_total" in r.text
    assert "aegis_model_request_duration_seconds" in r.text


def test_requests_are_counted_by_route_template(api: TestClient) -> None:
    labels = {"method": "GET", "route": "/api/v1/evaluations/{run_id}", "status": "422"}
    before = value("aegis_http_requests_total", **labels)
    api.get("/api/v1/evaluations/not-a-uuid")  # path validation: 422
    assert value("aegis_http_requests_total", **labels) == before + 1
    count = value(
        "aegis_http_request_duration_seconds_count",
        method="GET",
        route="/api/v1/evaluations/{run_id}",
    )
    assert count >= 1


def test_unknown_paths_share_one_label(client: TestClient) -> None:
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = value("aegis_http_requests_total", **labels)
    client.get("/no/such/path/123")
    client.get("/another/unknown")
    assert value("aegis_http_requests_total", **labels) == before + 2


def test_rejected_requests_are_counted(client: TestClient) -> None:
    labels = {"method": "GET", "route": "unmatched", "status": "400"}
    before = value("aegis_http_requests_total", **labels)
    client.get("/api/v1/documents", headers={"host": "evil.example"})
    assert value("aegis_http_requests_total", **labels) == before + 1


def test_server_errors_are_counted_as_500(client: TestClient) -> None:
    class Broken:
        model = "broken"

        def generate(self, system: str, prompt: str) -> Generation:
            raise RuntimeError("boom")

    ingest(client)
    app.dependency_overrides[get_chat_model] = Broken
    labels = {"method": "POST", "route": "/api/v1/chat", "status": "500"}
    before = value("aegis_http_requests_total", **labels)
    assert (
        client.post("/api/v1/chat", json={"question": "readiness probe"}).status_code
        == 500
    )
    assert value("aegis_http_requests_total", **labels) == before + 1


def test_chat_outcomes_and_retrieval_time(client: TestClient) -> None:
    ingest(client)
    answered = value("aegis_chat_answers_total", outcome="answered")
    low = value("aegis_chat_answers_total", outcome="refused_low_score")
    retrievals = value("aegis_retrieval_duration_seconds_count")
    client.post("/api/v1/chat", json={"question": "readiness probe traffic"})
    client.post("/api/v1/chat", json={"question": "sourdough recipe"})
    assert value("aegis_chat_answers_total", outcome="answered") == answered + 1
    assert value("aegis_chat_answers_total", outcome="refused_low_score") == low + 1
    assert value("aegis_retrieval_duration_seconds_count") == retrievals + 2


def test_ingest_results(client: TestClient) -> None:
    created = value("aegis_documents_ingested_total", result="created")
    duplicate = value("aegis_documents_ingested_total", result="duplicate")
    ingest(client, b"# Unique\n\nmetrics test document.\n")
    ingest(client, b"# Unique\n\nmetrics test document.\n")
    assert value("aegis_documents_ingested_total", result="created") == created + 1
    assert value("aegis_documents_ingested_total", result="duplicate") == duplicate + 1


def ollama(handler: object) -> OllamaClient:
    return OllamaClient(
        "http://ollama",
        1,
        1,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_model_calls_count_outcomes_tokens_and_time() -> None:
    def ok(_: httpx.Request) -> httpx.Response:
        body = {"message": {"content": "a"}, "prompt_eval_count": 7, "eval_count": 2}
        return httpx.Response(200, json=body)

    calls = value("aegis_model_requests_total", operation="chat", outcome="ok")
    prompt = value("aegis_model_tokens_total", kind="prompt")
    timed = value("aegis_model_request_duration_seconds_count", operation="chat")
    OllamaChat(ollama(ok), "m").generate("s", "q")
    assert (
        value("aegis_model_requests_total", operation="chat", outcome="ok") == calls + 1
    )
    assert value("aegis_model_tokens_total", kind="prompt") == prompt + 7
    assert (
        value("aegis_model_request_duration_seconds_count", operation="chat")
        == timed + 1
    )


@pytest.mark.parametrize(
    ("handler_result", "reason"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("down"), "unreachable"),
        (httpx.Response(404), "model_missing"),
        (httpx.Response(500), "http_error"),
        (httpx.Response(200, text="not json"), "bad_response"),
    ],
)
def test_model_failures_are_counted_by_reason(
    handler_result: object, reason: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if isinstance(handler_result, Exception):
            raise handler_result
        assert isinstance(handler_result, httpx.Response)
        return handler_result

    before = value("aegis_model_requests_total", operation="chat", outcome=reason)
    with pytest.raises(ModelUnavailable) as exc:
        OllamaChat(ollama(handler), "m").generate("s", "q")
    assert exc.value.reason == reason
    assert (
        value("aegis_model_requests_total", operation="chat", outcome=reason)
        == before + 1
    )


def test_evaluation_runs_publish_their_metrics(
    api: TestClient, settings: Settings
) -> None:
    checksum = load_dataset(settings.eval_dir / "v1.jsonl").checksum
    (settings.eval_dir / "v1.thresholds.json").write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": checksum,
                "basis": "test",
                "limits": {"failure_rate": 0},
            }
        ),
        encoding="utf-8",
    )
    done = value("aegis_evaluation_runs_total", status="done")
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert value("aegis_evaluation_runs_total", status="done") == done + 1
    assert value("aegis_evaluation_last_metric", metric="examples") == 3
    assert (
        value("aegis_evaluation_last_metric", metric="recall_at_k")
        == run["metrics"]["recall_at_k"]
    )
    assert REGISTRY.get_sample_value("aegis_evaluation_last_gate_passed") == 1


def test_failed_evaluation_runs_are_counted(api: TestClient) -> None:
    class Broken:
        model = "broken"

        def generate(self, system: str, prompt: str) -> Generation:
            raise RuntimeError("boom")

    app.dependency_overrides[get_chat_model] = Broken
    failed = value("aegis_evaluation_runs_total", status="failed")
    start(api)
    assert value("aegis_evaluation_runs_total", status="failed") == failed + 1


def test_model_refusal_is_counted_separately(client: TestClient) -> None:
    class Refuses:
        model = "refuses"

        def generate(self, system: str, prompt: str) -> Generation:
            return Generation("I don't know based on the provided documents.", 1, 1)

    ingest(client)
    app.dependency_overrides[get_chat_model] = Refuses
    before = value("aegis_chat_answers_total", outcome="refused_by_model")
    client.post("/api/v1/chat", json={"question": "readiness probe traffic"})
    assert value("aegis_chat_answers_total", outcome="refused_by_model") == before + 1


def test_embed_calls_are_labelled_embed() -> None:
    def bad(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    labels = {"operation": "embed", "outcome": "http_error"}
    before = value("aegis_model_requests_total", **labels)
    with pytest.raises(ModelUnavailable):
        OllamaEmbedder(ollama(bad), "m").embed(["x"])
    assert value("aegis_model_requests_total", **labels) == before + 1


def test_failed_gate_sets_gauge_to_zero(api: TestClient, settings: Settings) -> None:
    checksum = load_dataset(settings.eval_dir / "v1.jsonl").checksum
    (settings.eval_dir / "v1.thresholds.json").write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": checksum,
                "basis": "test",
                "limits": {"examples": 99},
            }
        ),
        encoding="utf-8",
    )
    start(api)
    assert REGISTRY.get_sample_value("aegis_evaluation_last_gate_passed") == 0


@pytest.mark.parametrize(
    ("body", "call"),
    [
        ({"message": {}}, "chat"),
        ({"embeddings": [[1.0], []]}, "embed"),
        ({"models": "not a list"}, "tags"),
    ],
)
def test_malformed_replies_count_as_bad_response_not_ok(
    body: dict[str, object], call: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = ollama(handler)
    ok = value("aegis_model_requests_total", operation=call, outcome="ok")
    bad = value("aegis_model_requests_total", operation=call, outcome="bad_response")
    with pytest.raises(ModelUnavailable):
        if call == "chat":
            OllamaChat(client, "m").generate("s", "q")
        elif call == "embed":
            OllamaEmbedder(client, "m").embed(["a", "b"])
        else:
            client.installed_models()
    assert value("aegis_model_requests_total", operation=call, outcome="ok") == ok
    assert (
        value("aegis_model_requests_total", operation=call, outcome="bad_response")
        == bad + 1
    )


def test_gate_gauge_is_nan_for_an_ungated_run(
    api: TestClient, settings: Settings
) -> None:
    import math

    checksum = load_dataset(settings.eval_dir / "v1.jsonl").checksum
    thresholds = settings.eval_dir / "v1.thresholds.json"
    thresholds.write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": checksum,
                "basis": "test",
                "limits": {"failure_rate": 0},
            }
        ),
        encoding="utf-8",
    )
    start(api)
    assert REGISTRY.get_sample_value("aegis_evaluation_last_gate_passed") == 1
    thresholds.unlink()  # the next run is not gated
    start(api)
    gauge = REGISTRY.get_sample_value("aegis_evaluation_last_gate_passed")
    assert gauge is not None and math.isnan(gauge)


def test_invalid_thresholds_fail_the_gate_everywhere(
    api: TestClient, settings: Settings
) -> None:
    # Fail closed: the API, the stored report and the metric all say "failed".
    (settings.eval_dir / "v1.thresholds.json").write_text("{not json", encoding="utf-8")
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["gate"] == {
        "passed": False,
        "failures": ["the thresholds file is invalid"],
    }
    assert run["artifact_path"] is not None
    report = (Path(run["artifact_path"]) / "report.md").read_text(encoding="utf-8")
    assert "Quality gate: **FAIL**" in report
    assert REGISTRY.get_sample_value("aegis_evaluation_last_gate_passed") == 0


def test_any_export_error_keeps_the_run_done_and_counted_once(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.evaluation import jobs

    def boom(*args: object, **kwargs: object) -> Path:
        raise RuntimeError("export exploded")

    monkeypatch.setattr(jobs, "_write_artifacts", boom)
    done = value("aegis_evaluation_runs_total", status="done")
    failed = value("aegis_evaluation_runs_total", status="failed")
    run = api.get(f"/api/v1/evaluations/{start(api)}").json()
    assert run["status"] == "done"
    assert run["artifact_path"] is None
    assert value("aegis_evaluation_runs_total", status="done") == done + 1
    assert value("aegis_evaluation_runs_total", status="failed") == failed
