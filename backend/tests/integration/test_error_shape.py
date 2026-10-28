from fastapi.testclient import TestClient


def test_unknown_path_and_wrong_method_use_the_error_shape(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    r = client.get("/api/v1/chat")
    assert (r.status_code, r.json()["error"]["code"]) == (405, "method_not_allowed")


def test_other_http_exceptions_use_generic_code() -> None:
    from fastapi import FastAPI
    from starlette.exceptions import HTTPException

    from app.core.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise HTTPException(418, "teapot")

    r = TestClient(app).get("/boom")
    assert (r.status_code, r.json()) == (
        418,
        {"error": {"code": "http_error", "message": "teapot"}},
    )
