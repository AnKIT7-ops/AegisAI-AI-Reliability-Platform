"""Ollama provider: embeddings and chat over Ollama's local HTTP API."""

import math
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.metrics import MODEL_DURATION, MODEL_REQUESTS, MODEL_TOKENS
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
        fault_delay_ms: int = 0,
        fault_error_rate: float = 0.0,
    ) -> None:
        self._fault_delay_s = fault_delay_ms / 1000
        self._fault_error_rate = fault_error_rate
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
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """One call, counted once: ok only if the reply also passes `validate`."""
        operation = path.rsplit("/", 1)[-1]  # chat, embed, tags
        start = time.perf_counter()
        try:
            self._inject_faults()
            data = self._request(method, path, payload, timeout)
            if validate is not None:
                validate(data)
        except ModelUnavailable as exc:
            MODEL_REQUESTS.labels(operation, exc.reason).inc()
            raise
        finally:
            MODEL_DURATION.labels(operation).observe(time.perf_counter() - start)
        MODEL_REQUESTS.labels(operation, "ok").inc()
        return data

    def _inject_faults(self) -> None:
        # Monitoring drills only; both settings are 0 unless deliberately set.
        if self._fault_delay_s:
            time.sleep(self._fault_delay_s)
        if self._fault_error_rate and random.random() < self._fault_error_rate:
            raise ModelUnavailable(
                "injected fault (AEGIS_FAULT_ERROR_RATE)", "injected"
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: Any,
        timeout: httpx.Timeout | None,
    ) -> dict[str, Any]:
        try:
            r = self._http.request(
                method, path, json=payload, timeout=timeout or httpx.USE_CLIENT_DEFAULT
            )
        except httpx.TimeoutException:
            raise ModelUnavailable("Ollama timed out", "timeout") from None
        except httpx.HTTPError:  # connection, protocol, decoding, bad URL
            raise ModelUnavailable("Ollama is not reachable", "unreachable") from None
        if r.status_code == 404 and isinstance(payload, dict) and "model" in payload:
            raise ModelUnavailable(
                f"model {payload['model']!r} is not installed", "model_missing"
            )
        if r.status_code != 200:
            raise ModelUnavailable(
                f"Ollama returned HTTP {r.status_code}", "http_error"
            )
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ModelUnavailable("Ollama returned an unexpected response")
        return data

    def installed_models(self) -> list[str]:
        # A status probe must fail fast, not wait out the generation timeout.
        def check(data: dict[str, Any]) -> None:
            if not isinstance(data.get("models"), list):
                raise ModelUnavailable("Ollama returned an unexpected response")

        data = self.request(
            "GET", "/api/tags", timeout=self._probe_timeout, validate=check
        )
        models = data["models"]
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

            def check(data: dict[str, Any], n: int = len(batch)) -> None:
                vectors = data.get("embeddings")
                if (
                    not isinstance(vectors, list)
                    or len(vectors) != n
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

            data = self.client.request(
                "POST",
                "/api/embed",
                {"model": self.model, "input": batch},
                validate=check,
            )
            vectors = data["embeddings"]
            dims.add(len(vectors[0]))
            out.extend(vectors)
        return out


class OllamaChat:
    def __init__(self, client: OllamaClient, model: str) -> None:
        self.client = client
        self.model = model

    def generate(self, system: str, prompt: str) -> Generation:
        def check(data: dict[str, Any]) -> None:
            message = data.get("message")
            if not isinstance(message, dict) or not isinstance(
                message.get("content"), str
            ):
                raise ModelUnavailable("Ollama returned an unexpected response")

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
            validate=check,
        )
        gen = Generation(
            data["message"]["content"].strip(),
            _count(data.get("prompt_eval_count")),
            _count(data.get("eval_count")),
        )
        if gen.prompt_tokens is not None:
            MODEL_TOKENS.labels("prompt").inc(gen.prompt_tokens)
        if gen.completion_tokens is not None:
            MODEL_TOKENS.labels("completion").inc(gen.completion_tokens)
        return gen


def _count(value: object) -> int | None:
    # Token counts are informational: anything odd becomes "unknown", not an error.
    return value if isinstance(value, int) and not isinstance(value, bool) else None
