from fastapi.testclient import TestClient


def test_demo_routes_are_gone(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/demo"),
        ("post", "/api/demo/load"),
        ("get", "/api/demo/status"),
    ):
        assert getattr(client, method)(path).status_code in (401, 404, 405)
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "/demo" in p]
