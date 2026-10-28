"""Document ingestion and listing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, UploadFile
from pydantic import BaseModel

from app.api.deps import get_embedder, get_store
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.services.llm.base import Embedder
from app.services.rag.ingest import ingest_document
from app.services.rag.store import DocumentRecord, VectorStore

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class IngestResponse(BaseModel):
    document: DocumentRecord
    created: bool


class DocumentList(BaseModel):
    documents: list[DocumentRecord]


# ponytail: Starlette spools the whole multipart body before this runs, so the
# size limit bounds what we read, not what the client sends. Ports are
# localhost-only; add a body-size limit in a reverse proxy before exposing the API.
@router.post("/ingest", status_code=201)
def ingest(
    file: UploadFile,
    response: Response,
    store: Annotated[VectorStore, Depends(get_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestResponse:
    """Upload one .md or .txt file. 201 when new, 200 if the content already exists."""
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise ApiError(
            413, "file_too_large", f"limit is {settings.max_upload_bytes} bytes"
        )
    record, created = ingest_document(
        file.filename or "", data, store, embedder, settings
    )
    if not created:
        response.status_code = 200
    return IngestResponse(document=record, created=created)


@router.get("")
def list_documents(store: Annotated[VectorStore, Depends(get_store)]) -> DocumentList:
    return DocumentList(documents=store.documents())
