import re

import pytest

from app.services.rag.chunking import chunk_text


def words(s: str) -> set[str]:
    return set(re.findall(r"\w+", s))


def test_empty_and_blank_text() -> None:
    assert chunk_text("", 100, 10) == []
    assert chunk_text("\n\n   \n", 100, 10) == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("# Title\n\nSome text.", 100, 10) == ["# Title\n\nSome text."]


def test_paragraphs_are_packed_and_overlap() -> None:
    paras = [f"para{i} " + "word " * 15 for i in range(6)]
    chunks = chunk_text("\n\n".join(paras), 200, 30)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        # the next chunk opens with text from the end of the previous one
        assert nxt.split("\n\n")[0] in prev
    assert all(len(c) <= 200 + 30 + 2 for c in chunks)


def test_long_paragraph_splits_on_spaces_and_hard_cuts() -> None:
    spaced = " ".join(f"w{i}" for i in range(300))
    chunks = chunk_text(spaced, 100, 0)
    assert all(len(c) <= 100 for c in chunks)
    assert words(" ".join(chunks)) >= words(spaced)

    solid = "x" * 250
    assert chunk_text(solid, 100, 0) == ["x" * 100, "x" * 100, "x" * 50]


def test_no_text_is_lost_and_output_is_deterministic() -> None:
    text = "\n\n".join(
        f"Section {i}. " + "alpha beta gamma " * (i + 3) for i in range(20)
    )
    a = chunk_text(text, 150, 25)
    assert a == chunk_text(text, 150, 25)
    assert words(" ".join(a)) == words(text)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, -1)])
def test_invalid_parameters(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("text", size, overlap)
