import pytest

from app.services.evaluation.dataset import Example
from app.services.evaluation.metrics import (
    Outcome,
    aggregate,
    cited_numbers,
    fact_present,
    first_relevant_rank,
    percentile,
    score_example,
)
from app.services.rag.answer import NO_ANSWER

ANSWERABLE = Example(
    id="a",
    category="answerable",
    question="q",
    expected_sources=["probes.md"],
    key_facts=["restart", ["15 days", "15d"]],
)
UNANSWERABLE = Example(id="u", category="unanswerable", question="q")
INJECTION = Example(id="i", category="injection", question="q", forbidden=["BANANA"])


def test_first_relevant_rank() -> None:
    assert first_relevant_rank(["x.md", "probes.md", "probes.md"], ["probes.md"]) == 2
    assert first_relevant_rank(["x.md"], ["probes.md"]) is None
    assert first_relevant_rank([], ["probes.md"]) is None


def test_cited_numbers_are_unique_and_sorted() -> None:
    assert cited_numbers("a [2] b [1][2] c [10]") == [1, 2, 10]
    assert cited_numbers("no citations") == []


def test_fact_alternatives_and_case() -> None:
    assert fact_present("Retention is 15D by default", ["15 days", "15d"])
    assert fact_present("It RESTARTS the container", "restart")
    assert not fact_present("It waits", "restart")
    assert fact_present("don’t", "don't")  # curly apostrophe normalized


def test_correct_answer_scores() -> None:
    o = Outcome(
        answer="The kubelet restarts it; retention is 15d [1].",
        retrieved=["probes.md", "x.md"],
        sources=["probes.md"],
    )
    assert score_example(ANSWERABLE, o) == {
        "error": False,
        "refused": False,
        "rank": 1,
        "facts_found": 2,
        "correct": True,
        "citation_correct": True,
    }


@pytest.mark.parametrize(
    ("answer", "sources", "expected"),
    [
        ("restart 15d", ["probes.md"], False),  # no citation
        ("restart 15d [3]", ["probes.md"], False),  # cites a missing source
        ("restart 15d [1]", ["other.md"], False),  # cites only an unexpected doc
        ("restart 15d [1][2]", ["other.md", "probes.md"], True),
    ],
)
def test_citation_correctness(answer: str, sources: list[str], expected: bool) -> None:
    o = Outcome(answer=answer, retrieved=sources, sources=sources)
    assert score_example(ANSWERABLE, o)["citation_correct"] is expected


def test_refused_answerable_is_incorrect_and_has_no_citation_score() -> None:
    s = score_example(ANSWERABLE, Outcome(answer=NO_ANSWER, retrieved=["x.md"]))
    assert s["refused"] and not s["correct"]
    assert s["rank"] is None
    assert "citation_correct" not in s


def test_missing_fact_is_incorrect() -> None:
    o = Outcome(answer="restart [1]", retrieved=["probes.md"], sources=["probes.md"])
    s = score_example(ANSWERABLE, o)
    assert (s["facts_found"], s["correct"]) == (1, False)


def test_injection_resistance() -> None:
    assert score_example(INJECTION, Outcome(answer="banana!"))["resisted"] is False
    assert score_example(INJECTION, Outcome(answer="15 days [1]"))["resisted"] is True


def test_errors_score_as_errors_only() -> None:
    assert score_example(ANSWERABLE, Outcome(error="Ollama timed out")) == {
        "error": True
    }


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) is None
    assert percentile([5], 95) == 5
    values = list(range(1, 101))
    assert (percentile(values, 50), percentile(values, 95)) == (50, 95)


def test_aggregate() -> None:
    examples = [ANSWERABLE, ANSWERABLE, UNANSWERABLE, INJECTION, ANSWERABLE]
    outcomes = [
        Outcome(
            answer="restart 15d [1]",
            retrieved=["probes.md"],
            sources=["probes.md"],
            latency_ms=100,
            prompt_tokens=50,
            completion_tokens=5,
        ),
        Outcome(answer=NO_ANSWER, retrieved=["x.md", "probes.md"], latency_ms=10),
        Outcome(answer=NO_ANSWER, latency_ms=20),
        Outcome(answer="BANANA", latency_ms=300, prompt_tokens=40, completion_tokens=1),
        Outcome(error="Ollama timed out"),
    ]
    scores = [score_example(e, o) for e, o in zip(examples, outcomes, strict=True)]
    assert aggregate(examples, outcomes, scores) == {
        "examples": 5,
        "errors": 1,
        "failure_rate": 0.2,
        "recall_at_k": 1.0,
        "mrr": 0.75,
        "answer_correctness": 0.5,
        "citation_correctness": 1.0,
        "refusal_accuracy": 0.6667,
        "false_refusal_rate": 0.5,
        "correct_refusal_rate": 1.0,
        "injection_resistance": 0.0,
        "latency_ms_p50": 20,
        "latency_ms_p95": 300,
        "prompt_tokens_total": 90,
        "completion_tokens_total": 6,
    }


def test_aggregate_with_an_empty_category_reports_none() -> None:
    o = Outcome(answer=NO_ANSWER)
    m = aggregate([UNANSWERABLE], [o], [score_example(UNANSWERABLE, o)])
    assert m["recall_at_k"] is None and m["injection_resistance"] is None
    assert m["correct_refusal_rate"] == 1.0
