from collections import Counter
from pathlib import Path

import pytest

from app.services.evaluation.dataset import Example, load_dataset

ROOT = Path(__file__).parents[3]
V1 = ROOT / "data" / "evaluation" / "v1.jsonl"
DOCS = {p.name for p in (ROOT / "data" / "sample_docs").glob("*.md")} - {
    "ATTRIBUTION.md"
}
UNANSWERABLE = '{"id": "x", "category": "unanswerable", "question": "q"}'


def test_v1_loads_with_the_planned_mix() -> None:
    ds = load_dataset(V1)
    assert ds.version == "v1"
    assert len(ds.checksum) == 16
    assert 50 <= len(ds.examples) <= 100
    counts = Counter(e.category for e in ds.examples)
    assert counts["answerable"] >= 40
    assert counts["unanswerable"] >= 10
    assert counts["injection"] >= 8


def test_v1_expected_sources_exist_and_cover_every_document() -> None:
    examples = load_dataset(V1).examples
    for e in examples:
        assert set(e.expected_sources) <= DOCS, e.id
    assert {s for e in examples for s in e.expected_sources} == DOCS


def write(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(write(tmp_path, UNANSWERABLE, UNANSWERABLE))


def test_bad_line_names_the_line(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="line 2"):
        load_dataset(write(tmp_path, UNANSWERABLE, '{"id": "y"}'))


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no examples"):
        load_dataset(write(tmp_path, "", "  "))


@pytest.mark.parametrize(
    "fields",
    [
        {"category": "answerable"},  # no sources or facts
        {"category": "answerable", "expected_sources": ["a.md"]},  # no facts
        {"category": "unanswerable", "key_facts": ["x"]},
        {"category": "injection"},  # no forbidden phrases
        {"category": "other"},
        {"category": "unanswerable", "extra": 1},
    ],
)
def test_example_validation(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Example.model_validate({"id": "x", "question": "q"} | fields)


def test_checksum_changes_with_content(tmp_path: Path) -> None:
    a = load_dataset(write(tmp_path, UNANSWERABLE))
    b = load_dataset(write(tmp_path, UNANSWERABLE.replace('"q"', '"r"')))
    assert a.checksum != b.checksum


def test_v1_key_facts_cannot_be_copied_from_the_question() -> None:
    # A fact that appears in the question is "found" in any answer that echoes it.
    for e in load_dataset(V1).examples:
        question = e.question.lower()
        for fact in e.key_facts:
            for option in [fact] if isinstance(fact, str) else fact:
                assert option.lower() not in question, (e.id, option)


def test_checksum_ignores_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    lf.write_bytes(V1.read_bytes().replace(b"\r\n", b"\n"))
    crlf.write_bytes(lf.read_bytes().replace(b"\n", b"\r\n"))
    assert load_dataset(lf).checksum == load_dataset(crlf).checksum


def test_v1_checksum_matches_the_thresholds_file() -> None:
    from app.services.evaluation.report import load_thresholds

    thresholds = load_thresholds(V1.parent, "v1")
    assert thresholds is not None
    assert thresholds.dataset_checksum == load_dataset(V1).checksum
