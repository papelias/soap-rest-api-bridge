"""Exercise hosted-demo admission controls through the FastAPI entry point."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from soap_rest_ai import main
from soap_rest_ai.api import demo
from soap_rest_ai.config import Settings

ASK = {"question": "What is the capital of Japan?"}
ALICE = {"X-Demo-User": "alice"}
ANSWER = {
    "answer": "Tokyo.",
    "provenance": {
        "operation": "CapitalCity",
        "parameters": {"sCountryISOCode": "JP"},
        "rest_endpoint": "/countries/JP/capital",
    },
}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, demo_mode=True)


@pytest_asyncio.fixture
async def service(
    settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncMock]:
    monkeypatch.setattr(main, "Settings", lambda: settings)
    async with main.app.router.lifespan_context(main.app):
        ask = AsyncMock(return_value=ANSWER)
        monkeypatch.setattr(main.app.state.nl_service, "ask", ask)
        yield ask


@pytest_asyncio.fixture
async def api(service: AsyncMock) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://demo.test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_demo_requires_proxy_identity_and_peer_to_be_loopback(
    api: httpx.AsyncClient, service: AsyncMock,
) -> None:
    response = await api.post("/ask", json=ASK)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "demo_proxy_required"
    assert (await api.get("/health")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, client=("192.0.2.10", 8000)),
        base_url="http://demo.test",
    ) as external:
        assert (await external.post("/ask", json=ASK, headers=ALICE)).status_code == 403
        assert (await external.get("/health")).status_code == 403
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_redirect_preserves_https_and_unauthed_slash_is_blocked(
    api: httpx.AsyncClient, service: AsyncMock,
) -> None:
    assert (await api.post("/ask/", json=ASK)).status_code == 403
    response = await api.post("/ask/", json=ASK, headers=ALICE)
    assert response.status_code == 307
    assert response.headers["Location"] == "https://demo.test/ask"
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_fixed_and_chunked_bodies_are_rejected_before_processing(
    api: httpx.AsyncClient, service: AsyncMock,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * (32 * 1024)
        yield b"x"

    for body in (b"x" * (32 * 1024 + 1), chunks()):
        response = await api.post("/ask", headers=ALICE, content=body)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "demo_body_too_large"
    service.assert_not_awaited()
    assert (await api.post("/ask", headers=ALICE, json=ASK)).status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_is_per_invite_and_resets(
    api: httpx.AsyncClient, service: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo, "monotonic", lambda: 63.0)
    for _ in range(6):
        assert (await api.post("/ask", headers=ALICE, json=ASK)).status_code == 200
    response = await api.post("/ask", headers=ALICE, json=ASK)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "demo_rate_limit"
    assert response.headers["Retry-After"] == "57"
    assert (await api.post("/ask", headers={"X-Demo-User": "bob"}, json=ASK)).status_code == 200
    monkeypatch.setattr(demo, "monotonic", lambda: 120.0)
    assert (await api.post("/ask", headers=ALICE, json=ASK)).status_code == 200
    assert service.await_count == 8


@pytest.mark.asyncio
async def test_daily_budget_is_shared_and_rejects_before_model_calls(
    settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.demo_daily_ask_limit = 1
    monkeypatch.setattr(main, "Settings", lambda: settings)
    async with main.app.router.lifespan_context(main.app):
        ask = AsyncMock(return_value=ANSWER)
        monkeypatch.setattr(main.app.state.nl_service, "ask", ask)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://demo.test",
        ) as client:
            assert (await client.post("/ask", headers=ALICE, json=ASK)).status_code == 200
            response = await client.post("/ask", headers={"X-Demo-User": "bob"}, json=ASK)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "demo_daily_limit"
    assert int(response.headers["Retry-After"]) > 0
    ask.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrency_limit_releases_slot_after_cancellation(
    api: httpx.AsyncClient, service: AsyncMock,
) -> None:
    both_started = asyncio.Event()
    unblock = asyncio.Event()

    async def wait_for_release(question: str) -> dict:
        if service.await_count == 2:
            both_started.set()
        await unblock.wait()
        return ANSWER

    service.side_effect = wait_for_release
    tasks = [asyncio.create_task(api.post("/ask", headers=ALICE, json=ASK)) for _ in range(2)]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=5)
        response = await api.post("/ask", headers={"X-Demo-User": "bob"}, json=ASK)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "demo_busy"
        assert service.await_count == 2
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        unblock.set()
        assert (await api.post("/ask", headers=ALICE, json=ASK)).status_code == 200
        assert (await tasks[1]).status_code == 200
    finally:
        unblock.set()
        await asyncio.gather(*tasks, return_exceptions=True)
