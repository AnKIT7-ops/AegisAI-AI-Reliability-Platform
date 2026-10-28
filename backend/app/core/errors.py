"""One error shape for every API failure: {"error": {"code": ..., "message": ...}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.services.llm.base import ModelUnavailable
from app.services.rag.store import IndexMismatch

log = logging.getLogger("aegis")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    # Framework errors (unknown path, wrong method) get the same shape.
    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(ModelUnavailable)
    async def model_unavailable(_: Request, exc: ModelUnavailable) -> JSONResponse:
        return error_response(503, "model_unavailable", str(exc))

    # The detail names server paths, so it goes to the log, not the client.
    @app.exception_handler(IndexMismatch)
    async def index_mismatch(_: Request, exc: IndexMismatch) -> JSONResponse:
        log.error("index mismatch: %s", exc)
        return error_response(
            500, "index_mismatch", "the document index needs to be rebuilt; re-ingest"
        )

    # Messages only, never the submitted input, so rejected text isn't echoed back.
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = [
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}"
            for e in exc.errors()
        ]
        return error_response(422, "invalid_request", "; ".join(parts))
