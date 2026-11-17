"""Keep the normal test suite offline; MockTransport remains usable."""

from typing import Any

import httpx
import httpx2
import pytest
import requests


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External HTTP is disabled in normal tests")

    async def reject_async(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External HTTP is disabled in normal tests")

    monkeypatch.setattr(requests.Session, "request", reject)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_async)
