"""Manual validation command behavior without live services."""

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from soap_rest_ai.config import Settings
from soap_rest_ai.nl import validate_live


@pytest.mark.asyncio
async def test_live_validation_reports_missing_configuration_without_http(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        _env_file=None, anthropic_api_key=None, anthropic_router_model="",
        anthropic_answer_model="", soap_wsdl_url="",
    )
    factory = MagicMock(side_effect=AssertionError("No HTTP client should be created"))
    monkeypatch.setattr(validate_live, "Settings", lambda: settings)
    monkeypatch.setattr(validate_live.httpx, "AsyncClient", factory)
    assert await validate_live.validate() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert "ANTHROPIC_API_KEY" in report["missing_settings"]
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_live_command_checks_five_questions_and_provenance(
    monkeypatch: pytest.MonkeyPatch, failure: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        _env_file=None, anthropic_api_key=SecretStr("test-only-key"),
        anthropic_router_model="test-router", anthropic_answer_model="test-answer",
        soap_wsdl_url="http://example.test/wsdl", facade_base_url="http://example.test",
    )
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        question = json.loads(request.content)["question"]
        case = next(case for case in validate_live.CASES if case[0] == question)
        _, operation, parameters, endpoint = case
        return httpx.Response(200, json={
            "answer": "Representative answer.",
            "provenance": {"operation": "WrongOperation" if failure else operation,
                           "parameters": parameters, "rest_endpoint": endpoint},
        })

    original_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(validate_live, "Settings", lambda: settings)
    monkeypatch.setattr(validate_live.httpx, "AsyncClient", factory)
    assert await validate_live.validate() == (1 if failure else 0)
    assert len(requests) == 5
    assert all(request.method == "POST" and request.url.path == "/ask" for request in requests)
    assert "test-only-key" not in capsys.readouterr().out
