import asyncio
import json
from uuid import uuid4

import pytest
from app.documents.citations import QuoteRequest, resolve_quote
from app.documents.grounded_answer import grounded_answer
from app.documents.service import DocumentDTO, SourceChunkDTO, SourceTextDTO
from app.providers.types import StreamDone, TextDelta


@pytest.fixture
def source():
    return SourceTextDTO(
        document=DocumentDTO(
            id=uuid4(),
            notebook_id=uuid4(),
            filename="test.txt",
            version_id=uuid4(),
            status="ready",
            error_code=None,
        ),
        items=[
            SourceChunkDTO(
                id=uuid4(), ordinal=0, page=None, text="Die Probe ist blau.", truncated=False
            )
        ],
        next_ordinal=None,
    )


def test_quote_points_to_server_selected_version_and_chunk(source):
    request = QuoteRequest(chunk_id=source.items[0].id, quote="Probe ist blau")
    result = resolve_quote(source, source, request)
    assert (result.document_id, result.version_id, result.chunk_id, result.quote) == (
        source.document.id,
        source.document.version_id,
        source.items[0].id,
        "Probe ist blau",
    )


def test_stale_selection_never_calls_execution(source):
    from unittest.mock import AsyncMock, Mock

    execution = Mock()
    read_page = AsyncMock(return_value=source)
    with pytest.raises(ValueError, match="source_unavailable"):
        asyncio.run(
            grounded_answer(
                actor_id=uuid4(),
                connection_id=uuid4(),
                notebook_id=source.document.notebook_id,
                document_id=source.document.id,
                expected_version_id=uuid4(),
                model="test",
                question="Welche Farbe?",
                read_page=read_page,
                execution=execution,
            )
        )
    read_page.assert_awaited_once()
    execution.stream.assert_not_called()


@pytest.mark.parametrize("revoked", [False, True])
def test_answer_rechecks_user_source_after_provider(source, revoked):
    actor, connection = uuid4(), uuid4()
    reads = []

    async def read_page(user, notebook, document):
        assert (user, notebook, document) == (
            actor,
            source.document.notebook_id,
            source.document.id,
        )
        reads.append(user)
        if revoked and len(reads) == 2:
            raise PermissionError
        return source

    class Execution:
        async def stream(self, *, actor_id, connection_id, messages, **options):
            assert (actor_id, connection_id) == (actor, connection)
            assert json.loads(messages[1].content)["source"][0]["text"] == source.items[0].text
            yield TextDelta(
                json.dumps(
                    {
                        "answer": "Blau.",
                        "citation": {"chunk_id": str(source.items[0].id), "quote": "blau"},
                    }
                )
            )
            yield StreamDone("test", "stop")

    call = grounded_answer(
        actor_id=actor,
        connection_id=connection,
        notebook_id=source.document.notebook_id,
        document_id=source.document.id,
        expected_version_id=source.document.version_id,
        model="test",
        question="Welche Farbe?",
        read_page=read_page,
        execution=Execution(),
    )
    if revoked:
        with pytest.raises(PermissionError):
            asyncio.run(call)
    else:
        assert asyncio.run(call).citation.quote == "blau"
    assert reads == [actor, actor]


@pytest.mark.parametrize(
    "change", ["version_id", "notebook_id", "id", "missing", "invented", "empty"]
)
def test_does_not_publish_unverifiable_quote(source, change):
    current = source.model_copy(deep=True)
    quote = "Probe"
    if change == "missing":
        current.items.clear()
    elif change == "invented":
        quote = "rot"
    elif change == "empty":
        quote = " "
    else:
        current = current.model_copy(
            update={"document": current.document.model_copy(update={change: uuid4()})}
        )
    with pytest.raises(ValueError, match="unverifiable_citation"):
        resolve_quote(source, current, QuoteRequest(chunk_id=source.items[0].id, quote=quote))
