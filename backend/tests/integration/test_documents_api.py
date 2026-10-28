from fastapi.testclient import TestClient

DOC = b"# Probes\n\nA readiness probe decides whether a pod receives traffic.\n"


def upload(client: TestClient, name: str, data: bytes) -> tuple[int, dict[str, object]]:
    r = client.post(
        "/api/v1/documents/ingest", files={"file": (name, data, "text/markdown")}
    )
    return r.status_code, r.json()


def test_ingest_then_list(client: TestClient) -> None:
    status, body = upload(client, "probes.md", DOC)
    assert status == 201
    assert body["created"] is True
    doc = body["document"]
    assert isinstance(doc, dict)
    assert doc["filename"] == "probes.md"
    assert doc["chunks"] == 1
    assert doc["size_bytes"] == len(DOC)

    listed = client.get("/api/v1/documents").json()["documents"]
    assert [d["id"] for d in listed] == [doc["id"]]


def test_same_content_is_not_ingested_twice(client: TestClient) -> None:
    upload(client, "a.md", DOC)
    status, body = upload(client, "b.md", DOC)
    assert status == 200
    assert body["created"] is False
    assert len(client.get("/api/v1/documents").json()["documents"]) == 1


def test_client_path_is_stripped_from_filename(client: TestClient) -> None:
    _, body = upload(client, "..\\..\\x/../evil.txt", b"plain text")
    assert body["document"]["filename"] == "evil.txt"  # type: ignore[index]


def test_rejections_use_the_error_format(client: TestClient) -> None:
    cases = [
        ("notes.pdf", DOC, 415, "unsupported_type"),
        ("noext", DOC, 415, "unsupported_type"),
        ("big.md", b"a " * 6_000, 413, "file_too_large"),
        ("empty.md", b"\n\n  \n", 400, "empty_document"),
        ("latin1.txt", "caf\xe9".encode("latin-1"), 400, "invalid_encoding"),
    ]
    for name, data, status, code in cases:
        got_status, body = upload(client, name, data)
        assert (got_status, body["error"]["code"]) == (status, code), name  # type: ignore[index]
    assert client.get("/api/v1/documents").json()["documents"] == []


def test_missing_file_is_a_422(client: TestClient) -> None:
    r = client.post("/api/v1/documents/ingest")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
