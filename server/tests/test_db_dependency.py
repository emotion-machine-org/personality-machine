import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.request_context import RequestContext, clear_request_context, set_request_context


class DummyConnection:
    pass


async def test_get_db_reuses_request_context_connection():
    conn = DummyConnection()
    set_request_context(RequestContext(request_id="test", _conn=conn))

    try:
        dep = get_db()
        yielded = await anext(dep)

        assert yielded is conn
    finally:
        clear_request_context()
        await dep.aclose()


async def test_get_db_reused_context_propagates_downstream_exception():
    conn = DummyConnection()
    set_request_context(RequestContext(request_id="test", _conn=conn))
    dep = get_db()

    try:
        yielded = await anext(dep)
        assert yielded is conn

        with pytest.raises(RuntimeError, match="boom"):
            await dep.athrow(RuntimeError("boom"))
    finally:
        clear_request_context()
        await dep.aclose()


def test_fastapi_reuses_get_db_for_nested_and_route_dependency():
    app = FastAPI()
    calls = 0

    async def fake_get_db():
        nonlocal calls
        calls += 1
        yield object()

    async def auth(conn=Depends(fake_get_db)):
        return conn

    @app.get("/probe")
    async def probe(subject=Depends(auth), conn=Depends(fake_get_db)):
        return {"same": subject is conn}

    response = TestClient(app).get("/probe")

    assert response.status_code == 200
    assert response.json() == {"same": True}
    assert calls == 1
