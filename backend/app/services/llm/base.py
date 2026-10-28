"""Model provider interfaces and the deterministic fakes used without a real model."""

import hashlib
import math
import re
from typing import Protocol


class ModelUnavailable(Exception):
    """The provider can't serve the request: down, timeout, no model, bad reply."""


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatModel(Protocol):
    model: str

    def generate(self, system: str, prompt: str) -> str: ...


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get similar vectors. No model needed."""

    model = "fake-embed"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                h = int.from_bytes(
                    hashlib.blake2b(word.encode(), digest_size=4).digest()
                )
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeChat:
    """Echoes the first numbered context block, so tests see what the model got."""

    model = "fake-chat"

    def generate(self, system: str, prompt: str) -> str:
        m = re.search(
            r"^\[1\][^\n]*\n(.+?)(?:\n\n\[\d+\]|\n</context>)", prompt, re.M | re.S
        )
        return f"[1] {m.group(1)[:200]}" if m else "I don't know."
