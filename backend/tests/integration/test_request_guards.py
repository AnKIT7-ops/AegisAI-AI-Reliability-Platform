import pytest
from fastapi.testclient import TestClient

from app.main import app

DOC = {"file": ("a.md", b"# A\n\nsome text\n", "text/markdown")}


def test_foreign_host_is_rejected(client: TestClient) -> None:
    # DNS rebinding: the browser connects to 127.0.0.1 but sends the attacker's Host.
    r = TestClient(app, base_url="http://evil.example").get("/health/live")
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_host")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1:8000", "backend:8000"])
def test_local_hosts_are_allowed(client: TestClient, host: str) -> None:
    r = client.get("/health/live", headers={"host": host})
    assert r.status_code == 200


def test_cross_site_chat_post_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/api/v1/chat",
        json={"question": "q"},
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_site_post_is_rejected(client: TestClient, origin: str) -> None:
    r = client.post("/api/v1/documents/ingest", files=DOC, headers={"origin": origin})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_origin"
    # Nothing was ingested.
    assert client.get("/api/v1/documents").json()["documents"] == []


def test_allowed_origin_can_post(client: TestClient) -> None:
    r = client.post(
        "/api/v1/documents/ingest",
        files=DOC,
        headers={"origin": "http://localhost:8080"},
    )
    assert r.status_code == 201


def test_post_without_origin_is_allowed(client: TestClient) -> None:
    # curl and scripts send no Origin header.
    assert client.post("/api/v1/documents/ingest", files=DOC).status_code == 201


def test_cross_site_get_is_not_blocked(client: TestClient) -> None:
    # Reads are harmless without CORS: the browser won't hand the body to the page.
    r = client.get("/api/v1/documents", headers={"origin": "https://evil.example"})
    assert r.status_code == 200


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_cross_site_unsafe_methods_are_rejected(
    client: TestClient, method: str
) -> None:
    r = client.request(
        method, "/api/v1/documents/x", headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403


def test_host_rejection_uses_error_envelope(client: TestClient) -> None:
    r = client.get("/health/live", headers={"host": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_host"


@pytest.mark.parametrize("origin", ["http://localhost", "http://LOCALHOST"])
def test_same_origin_post_is_allowed(client: TestClient, origin: str) -> None:
    # The /docs page posts to its own origin; browsers send Origin even then.
    headers = {"origin": origin.lower(), "host": origin.split("//")[1]}
    r = client.post("/api/v1/documents/ingest", files=DOC, headers=headers)
    assert r.status_code == 201


@pytest.mark.parametrize("host", ["LOCALHOST", "LocalHost:8000", "BACKEND"])
def test_host_allowlist_is_case_insensitive(client: TestClient, host: str) -> None:
    assert client.get("/health/live", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["localhost.evil.example", "EVIL.example:80"])
def test_lookalike_hosts_are_rejected(client: TestClient, host: str) -> None:
    r = client.get("/health/live", headers={"host": host})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_host")


def test_same_origin_check_is_case_insensitive_on_host(client: TestClient) -> None:
    r = client.post(
        "/api/v1/documents/ingest",
        files=DOC,
        headers={"origin": "http://localhost", "host": "LOCALHOST"},
    )
    assert r.status_code == 201


def test_other_port_on_allowed_host_is_not_same_origin(client: TestClient) -> None:
    r = client.post(
        "/api/v1/documents/ingest",
        files=DOC,
        headers={"origin": "http://localhost:9999", "host": "localhost"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden_origin")
