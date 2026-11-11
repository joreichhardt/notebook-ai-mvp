import asyncio

import httpx
import pytest
from app.providers.adapter import Adapter
from fastapi import HTTPException
from test_execution_adapter import provider


@pytest.mark.parametrize("kind", ["openai", "ollama"])
def test_embeddings_actual_identity_order_and_dimension(kind):
    def handler(request):
        if kind == "openai":
            assert request.url.path.endswith("/embeddings")
            body = {
                "model": "actual",
                "data": [{"index": 1, "embedding": [3, 4]}, {"index": 0, "embedding": [1, 2]}],
            }
        else:
            assert request.url.path.endswith("/api/embed")
            body = {"model": "actual", "embeddings": [[1, 2], [3, 4]]}
        return httpx.Response(200, json=body)

    result = asyncio.run(
        Adapter(provider(kind), "SYNTHETIC", httpx.MockTransport(handler)).embed(
            model="alias", texts=["one", "two"]
        )
    )
    assert result.model == "actual"
    assert result.dimensions == 2
    assert result.vectors == ((1.0, 2.0), (3.0, 4.0))


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "embedding": []}],
        [{"index": 1, "embedding": [1]}],
        [{"index": 0, "embedding": [True]}],
        [{"index": 0, "embedding": ["1"]}],
        [{"index": 0, "embedding": [float("inf")]}],
        [{"index": 0, "embedding": [1]}, {"index": 0, "embedding": [2]}],
        [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [2, 3]}],
    ],
)
def test_invalid_embeddings_rejected(rows):
    import json

    count = 2 if len(rows) == 2 else 1
    adapter = Adapter(
        provider(),
        "SYNTHETIC",
        httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps({"model": "actual", "data": rows}))
        ),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(adapter.embed(model="alias", texts=["one"] * count))
    assert error.value.status_code == 502
