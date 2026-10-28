import json
from collections.abc import Callable

import httpx
import pytest

from app.services.llm.base import ModelUnavailable
from app.services.llm.ollama import (
    EMBED_BATCH,
    OllamaChat,
    OllamaClient,
    OllamaEmbedder,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> OllamaClient:
    return OllamaClient("http://ollama", 1, 1, transport=httpx.MockTransport(handler))


def raises(exc: Exception) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_chat_sends_non_streaming_no_think_request() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(path=req.url.path, body=json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "  answer [1] "}})

    assert OllamaChat(client(handler), "qwen").generate("sys", "q").text == "answer [1]"
    body = seen["body"]
    assert seen["path"] == "/api/chat"
    assert isinstance(body, dict)
    assert body["model"] == "qwen"
    assert body["stream"] is False
    assert body["think"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_embed_batches_and_keeps_order() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        batch = json.loads(req.content)["input"]
        calls.append(len(batch))
        return httpx.Response(200, json={"embeddings": [[float(t)] for t in batch]})

    texts = [str(i) for i in range(EMBED_BATCH + 5)]
    vectors = OllamaEmbedder(client(handler), "nomic").embed(texts)
    assert calls == [EMBED_BATCH, 5]
    assert vectors == [[float(t)] for t in texts]


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (raises(httpx.ConnectError("refused")), "not reachable"),
        (raises(httpx.ReadTimeout("slow")), "timed out"),
        (lambda _: httpx.Response(404, json={"error": "not found"}), "not installed"),
        (lambda _: httpx.Response(500, text="boom"), "HTTP 500"),
        (lambda _: httpx.Response(200, text="not json"), "unexpected response"),
        (lambda _: httpx.Response(200, json=["a list"]), "unexpected response"),
        (lambda _: httpx.Response(200, json={"message": {}}), "unexpected response"),
    ],
)
def test_chat_errors_become_model_unavailable(handler: Handler, message: str) -> None:
    with pytest.raises(ModelUnavailable, match=message):
        OllamaChat(client(handler), "qwen").generate("sys", "q")


@pytest.mark.parametrize(
    "body",
    [{}, {"embeddings": [[0.1]]}, {"embeddings": [[0.1], []]}, {"embeddings": "x"}],
)
def test_malformed_embeddings(body: dict[str, object]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ModelUnavailable, match="malformed"):
        OllamaEmbedder(client(handler), "nomic").embed(["a", "b"])


def test_error_messages_do_not_leak_upstream_detail() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret internal trace")

    with pytest.raises(ModelUnavailable) as e:
        OllamaChat(client(handler), "qwen").generate("sys", "q")
    assert "secret" not in str(e.value)


def test_installed_models() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "a:latest"}, {"x": 1}]})

    assert client(handler).installed_models() == ["a:latest"]


@pytest.mark.parametrize(
    "body",
    [{"embeddings": [[0.1, 0.2], [0.3]]}, {"embeddings": [["a"], ["b"]]}],
    ids=["ragged", "non-numeric"],
)
def test_ragged_or_non_numeric_embeddings(body: dict[str, object]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ModelUnavailable, match="malformed"):
        OllamaEmbedder(client(handler), "nomic").embed(["a", "b"])


def test_other_httpx_errors_become_model_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("bad gzip")

    with pytest.raises(ModelUnavailable, match="not reachable"):
        OllamaChat(client(handler), "qwen").generate("sys", "q")


def test_status_probe_uses_a_short_timeout() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.extensions["timeout"])
        return httpx.Response(200, json={"models": []})

    OllamaClient(
        "http://ollama", 3, 120, transport=httpx.MockTransport(handler)
    ).installed_models()
    assert seen["read"] == 6
    assert seen["connect"] == 3


def test_boolean_embedding_values_are_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[True]]})

    with pytest.raises(ModelUnavailable, match="malformed"):
        OllamaEmbedder(client(handler), "nomic").embed(["a"])


def test_probe_404_is_http_error_not_missing_model() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(ModelUnavailable, match="HTTP 404"):
        client(handler).installed_models()


def test_installed_models_rejects_non_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": "x"})

    with pytest.raises(ModelUnavailable, match="unexpected response"):
        client(handler).installed_models()


def test_generation_keeps_the_long_client_timeout() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.extensions["timeout"])
        return httpx.Response(200, json={"message": {"content": "ok"}})

    c = OllamaClient("http://ollama", 3, 120, transport=httpx.MockTransport(handler))
    OllamaChat(c, "qwen").generate("s", "q")
    assert seen["read"] == 120


def test_embedding_size_must_match_across_batches() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        batch = json.loads(req.content)["input"]
        size = 2 if batch[0] == "0" else 3
        return httpx.Response(200, json={"embeddings": [[0.1] * size for _ in batch]})

    with pytest.raises(ModelUnavailable, match="malformed"):
        OllamaEmbedder(client(handler), "nomic").embed(
            [str(i) for i in range(EMBED_BATCH + 1)]
        )


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_non_finite_embeddings_rejected(bad: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f'{{"embeddings": [[0.1, {bad}]]}}')

    with pytest.raises(ModelUnavailable, match="malformed"):
        OllamaEmbedder(client(handler), "nomic").embed(["a"])


def test_chat_reports_token_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "message": {"content": " hi [1] "},
            "prompt_eval_count": 120,
            "eval_count": 9,
        }
        return httpx.Response(200, json=body)

    gen = OllamaChat(client(handler), "qwen").generate("sys", "q")
    assert (gen.text, gen.prompt_tokens, gen.completion_tokens) == ("hi [1]", 120, 9)


@pytest.mark.parametrize("bad", [None, "12", True, 1.5])
def test_odd_token_counts_become_unknown(bad: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"message": {"content": "hi"}, "prompt_eval_count": bad}
        return httpx.Response(200, json=body)

    assert OllamaChat(client(handler), "qwen").generate("s", "q").prompt_tokens is None
