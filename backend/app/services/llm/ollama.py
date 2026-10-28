"""Ollama provider: embeddings and chat over Ollama's local HTTP API."""

import math
from typing import Any

import httpx

from app.services.llm.base import Generation, ModelUnavailable

EMBED_BATCH = 32


class OllamaClient:
    """Bounded timeouts, one retry on connection failure only, every error mapped to
    ModelUnavailable with a message that is safe to show clients."""

    def __init__(
        self,
        base_url: str,
        connect_timeout_s: float,
        generate_timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(generate_timeout_s, connect=connect_timeout_s),
            transport=transport or httpx.HTTPTransport(retries=1),
        )
        self._probe_timeout = httpx.Timeout(
            connect_timeout_s * 2, connect=connect_timeout_s
        )

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        try:
            r = self._http.request(
                method, path, json=payload, timeout=timeout or httpx.USE_CLIENT_DEFAULT
            )
        except httpx.TimeoutException:
            raise ModelUnavailable("Ollama timed out") from None
        except httpx.HTTPError:  # connection, protocol, decoding, bad URL
            raise ModelUnavailable("Ollama is not reachable") from None
        if r.status_code == 404 and isinstance(payload, dict) and "model" in payload:
            raise ModelUnavailable(f"model {payload['model']!r} is not installed")
        if r.status_code != 200:
            raise ModelUnavailable(f"Ollama returned HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ModelUnavailable("Ollama returned an unexpected response")
        return data

    def installed_models(self) -> list[str]:
        # A status probe must fail fast, not wait out the generation timeout.
        models = self.request("GET", "/api/tags", timeout=self._probe_timeout).get(
            "models"
        )
        if not isinstance(models, list):
            raise ModelUnavailable("Ollama returned an unexpected response")
        return [m["name"] for m in models if isinstance(m, dict) and "name" in m]


class OllamaEmbedder:
    def __init__(self, client: OllamaClient, model: str) -> None:
        self.client = client
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        dims: set[int] = set()  # every batch must match the first one's size
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            data = self.client.request(
                "POST", "/api/embed", {"model": self.model, "input": batch}
            )
            vectors = data.get("embeddings")
            if (
                not isinstance(vectors, list)
                or len(vectors) != len(batch)
                or not all(isinstance(v, list) and v for v in vectors)
                or len(dims | {len(v) for v in vectors}) != 1
                or not all(
                    isinstance(x, int | float)
                    and not isinstance(x, bool)
                    and math.isfinite(x)
                    for v in vectors
                    for x in v
                )
            ):
                raise ModelUnavailable("Ollama returned malformed embeddings")
            dims.add(len(vectors[0]))
            out.extend(vectors)
        return out


class OllamaChat:
    def __init__(self, client: OllamaClient, model: str) -> None:
        self.client = client
        self.model = model

    def generate(self, system: str, prompt: str) -> Generation:
        data = self.client.request(
            "POST",
            "/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                # Thinking models otherwise spend most latency on hidden reasoning.
                "think": False,
                "options": {"temperature": 0},
            },
        )
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelUnavailable("Ollama returned an unexpected response")
        return Generation(
            content.strip(),
            _count(data.get("prompt_eval_count")),
            _count(data.get("eval_count")),
        )


def _count(value: object) -> int | None:
    # Token counts are informational: anything odd becomes "unknown", not an error.
    return value if isinstance(value, int) and not isinstance(value, bool) else None
