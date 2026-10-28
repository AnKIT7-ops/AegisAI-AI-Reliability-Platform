"""Versioned evaluation dataset: one JSON example per line (JSONL)."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Category = Literal["answerable", "unanswerable", "injection"]
# A key fact is a phrase that must appear in the answer; a list means any one of
# these phrasings counts.
KeyFact = str | list[str]


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    category: Category
    question: str = Field(min_length=1, max_length=2000)
    expected_sources: list[str] = []  # filenames; any one of them counts as a hit
    key_facts: list[KeyFact] = []
    forbidden: list[str] = []  # phrases the answer must not contain
    reviewed: bool = False  # checked against the documents by a person

    @model_validator(mode="after")
    def _targets_match_category(self) -> "Example":
        if self.category == "answerable" and not (
            self.expected_sources and self.key_facts
        ):
            raise ValueError("answerable examples need expected_sources and key_facts")
        if self.category == "unanswerable" and (
            self.expected_sources or self.key_facts
        ):
            raise ValueError("unanswerable examples have no sources or key facts")
        if self.category == "injection" and not self.forbidden:
            raise ValueError("injection examples need forbidden phrases")
        return self


class Dataset(BaseModel):
    version: str  # the file name without extension, e.g. "v1"
    checksum: str  # changes with the content; line endings are normalized to LF
    examples: list[Example]


def load_dataset(path: Path) -> Dataset:
    raw = path.read_bytes()
    examples = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            examples.append(Example.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"{path.name} line {n}: {exc}") from exc
    ids = [e.id for e in examples]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"{path.name}: duplicate example ids {dupes}")
    if not examples:
        raise ValueError(f"{path.name}: no examples")
    return Dataset(
        version=path.stem,
        # CRLF and LF copies are the same dataset (git may convert on checkout).
        checksum=hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()[:16],
        examples=examples,
    )
