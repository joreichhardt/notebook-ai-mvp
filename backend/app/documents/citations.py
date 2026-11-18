"""Resolve a provider's quote only against the current authorized source page."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.documents.service import SourceTextDTO


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)
    notebook_id: UUID
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    ordinal: int
    quote: str


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: UUID
    quote: Annotated[str, Field(min_length=1, max_length=512)]


def resolve_quote(sent: SourceTextDTO, current: SourceTextDTO, request: QuoteRequest) -> Citation:
    """Caller reads both pages as the same actor; never accept a model-supplied URL."""
    doc = current.document
    if (
        doc.status == sent.document.status == "ready"
        and doc.id == sent.document.id
        and doc.notebook_id == sent.document.notebook_id
        and doc.version_id == sent.document.version_id
        and request.quote.strip()
    ):
        original = next((item for item in sent.items if item.id == request.chunk_id), None)
        if original is not None and original in current.items and request.quote in original.text:
            return Citation(
                notebook_id=doc.notebook_id,
                document_id=doc.id,
                version_id=doc.version_id,
                chunk_id=original.id,
                ordinal=original.ordinal,
                quote=request.quote,
            )
    raise ValueError("unverifiable_citation")
