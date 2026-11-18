"""Offline tool coverage and a subprocess contract check using the MCP client."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from jsonschema import ValidationError, validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from soap_rest_ai import mcp_server

CAPITAL_TOOL = "get_country_capital"
CAPITAL = {"operation": "CapitalCity", "iso_code": "PH", "capital": "Manila"}
COUNTRY = {
    "iso_code": "PH", "name": "Philippines", "capital": "Manila",
    "phone_code": "63", "continent_code": "AS", "currency_code": "PHP",
    "flag_url": "https://example.test/ph.png",
    "languages": [None, {"iso_code": "eng", "name": "English"}],
}
CASES = [
    (CAPITAL_TOOL, "country_code", " ph ", "/countries/PH/capital", CAPITAL, "capital", 123),
    (
        "get_country_currency", "country_code", "ph", "/countries/PH/currency",
        {"operation": "CountryCurrency", "iso_code": "PH", "currency": {"iso_code": "PHP", "name": "Peso"}},
        "currency", {"iso_code": "PHP", "name": 12},
    ),
    (
        "get_country_phone_code", "country_code", "ph", "/countries/PH/phone-code",
        {"operation": "CountryIntPhoneCode", "iso_code": "PH", "phone_code": "63"},
        "phone_code", 63,
    ),
    (
        "get_country_info", "country_code", "ph", "/countries/PH",
        {"operation": "FullCountryInfo", "iso_code": "PH", "country": COUNTRY},
        "country", {**COUNTRY, "languages": [False]},
    ),
    (
        "get_countries_by_currency", "currency_code", " php ", "/currencies/PHP/countries",
        {
            "operation": "CountriesUsingCurrency", "currency_code": "PHP",
            "countries": [None, {"iso_code": "PH", "name": "Philippines"}],
        },
        "countries", {"iso_code": "PH", "name": "Philippines"},
    ),
]


async def run_lookup(handler, name=CAPITAL_TOOL, arguments=None):
    async with httpx.AsyncClient(
        base_url="http://facade.test", transport=httpx.MockTransport(handler),
    ) as client:
        return await mcp_server.run_lookup(
            client, mcp_server.LOOKUPS[name],
            {"country_code": "PH"} if arguments is None else arguments,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("name,argument,raw,path,payload,field,wrong_type", CASES)
async def test_routes_and_typed_results(name, argument, raw, path, payload, field, wrong_type):
    lookup = mcp_server.LOOKUPS[name]
    schema = lookup.tool(name).outputSchema
    code = raw.strip().upper()

    async def run(data):
        def handler(request):
            assert request.method == "GET"
            assert request.url.path == path
            return httpx.Response(200, json={**payload, field: data})

        result = await run_lookup(handler, name, {argument: raw})
        envelope = result.model_dump(mode="json")
        validate(envelope, schema)
        return envelope

    for value in (payload[field], None):
        assert await run(value) == {
            "success": True, "operation": payload["operation"],
            "input": {argument: code}, "data": value,
        }
    failed = await run(wrong_type)
    assert failed["error"]["code"] == "INVALID_UPSTREAM_RESPONSE"
    assert failed["data"] is None
    with pytest.raises(ValidationError):
        validate({
            "success": True, "operation": payload["operation"],
            "input": {argument: code}, "data": wrong_type,
        }, schema)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arguments"), [
    (CAPITAL_TOOL, {}),
    (CAPITAL_TOOL, {"country_code": None}),
    (CAPITAL_TOOL, {"country_code": 63}),
    (CAPITAL_TOOL, {"country_code": "P1"}),
    (CAPITAL_TOOL, {"country_code": "ＰＨ"}),
    (CAPITAL_TOOL, {"country_code": "ſH"}),
    (CAPITAL_TOOL, {"country_code": " PH/.. "}),
    (CAPITAL_TOOL, {"country_code": "PH", "path": "/ask"}),
    ("get_countries_by_currency", {"currency_code": "PH"}),
    ("get_countries_by_currency", {"currency_code": "P H"}),
    ("get_countries_by_currency", {"currency_code": "ẞAA"}),
])
async def test_invalid_input_makes_no_request(name, arguments):
    def handler(_):
        pytest.fail("Invalid input reached HTTP")

    result = await run_lookup(handler, name, arguments)
    assert result.success is False
    assert result.error.code == "INVALID_INPUT"
    assert result.input is result.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b"not json",
    b"[]",
    b'{"iso_code":"PH","capital":"Manila"}',
    b'{"operation":"CapitalCity","iso_code":"PH"}',
    b'{"operation":"CapitalCity","iso_code":"JP","capital":"Tokyo"}',
    b'{"operation":"CountryCurrency","iso_code":"PH","capital":"Manila"}',
])
async def test_invalid_success_response(body):
    result = await run_lookup(lambda _: httpx.Response(200, content=body))
    assert result.error.code == "INVALID_UPSTREAM_RESPONSE"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "metadata", "code"), [
    (422, {"code": "invalid_input"}, "INVALID_INPUT"),
    (403, {"code": "operation_blocked"}, "OPERATION_BLOCKED"),
    (502, {"code": "soap_fault", "retryable": True}, "UPSTREAM_FAULT"),
    (502, {"code": "invalid_upstream_response"}, "INVALID_UPSTREAM_RESPONSE"),
    (502, {"code": "validation_serialization"}, "INVALID_UPSTREAM_RESPONSE"),
    (503, {"code": "upstream_unavailable", "retryable": False}, "UPSTREAM_UNAVAILABLE"),
    (504, {"code": "upstream_timeout", "retryable": False}, "UPSTREAM_TIMEOUT"),
    (502, {"code": "internal_error"}, "FACADE_ERROR"),
    (404, {"code": "http_error"}, "FACADE_ERROR"),
    (500, {}, "FACADE_ERROR"),
])
async def test_nonretryable_failures(status, metadata, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {
            **metadata, "message": "secret error", "fault_code": "secret fault",
        }})

    result = await run_lookup(handler)
    assert len(calls) == 1
    assert result.error.code == code
    assert result.data is None
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, 502, 503, 504,
    {"code": "upstream_unavailable", "retryable": True},
])
async def test_transient_failures_retry_once(failure):
    calls = []

    def handler(request):
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 2:
            return httpx.Response(200, json=CAPITAL)
        if isinstance(failure, type):
            raise failure("internal transport details", request=request)
        if isinstance(failure, dict):
            return httpx.Response(503, json={"error": failure})
        return httpx.Response(failure, text="temporary gateway failure")

    result = await run_lookup(handler)
    assert result.success is True
    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.25


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapped", "header", "expected_delay"), [
    (False, None, None),
    (True, None, None),
    (True, "invalid", None),
    (False, "-1", None),
    (False, "0.5", None),
    (False, "Sun, 06 Nov 1994 08:49:37 GMT trailing text", None),
    (False, "7", None),
    (True, "0", 0.25),
    (False, "1", 1),
    (True, "past HTTP-date", 0.25),
])
async def test_rate_limits(wrapped, header, expected_delay):
    calls = []
    if header == "past HTTP-date":
        header = format_datetime(datetime.now(UTC) - timedelta(seconds=60), usegmt=True)

    def handler(_):
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 2:
            return httpx.Response(200, json=CAPITAL)
        headers = {} if header is None else {"Retry-After": header}
        if wrapped:
            return httpx.Response(503, headers=headers, json={"error": {
                "code": "upstream_unavailable", "upstream_status": 429, "retryable": True,
            }})
        return httpx.Response(429, headers=headers)

    result = await run_lookup(handler)
    if expected_delay is None:
        assert result.error.code == "UPSTREAM_RATE_LIMITED"
        assert len(calls) == 1
    else:
        assert result.success is True
        assert len(calls) == 2
        assert calls[1] - calls[0] >= expected_delay


@pytest.mark.asyncio
async def test_attempt_deadline_cancels_slow_requests(monkeypatch):
    assert mcp_server.ATTEMPT_TIMEOUT == 3
    monkeypatch.setattr(mcp_server, "ATTEMPT_TIMEOUT", 0.05)
    cancelled = []

    async def handler(_):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    async with asyncio.timeout(2):
        result = await run_lookup(handler)
    assert result.error.code == "UPSTREAM_TIMEOUT"
    assert cancelled == [True, True]


@pytest.mark.asyncio
async def test_call_deadline_includes_retry_wait(monkeypatch):
    assert mcp_server.CALL_TIMEOUT == 7
    monkeypatch.setattr(mcp_server, "CALL_TIMEOUT", 0.55)
    finished = False
    calls = 0

    async def handler(_):
        nonlocal calls, finished
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        await asyncio.sleep(2)
        finished = True
        return httpx.Response(200, json=CAPITAL)

    async with asyncio.timeout(1.5):
        result = await run_lookup(handler)
    assert calls == 2
    assert result.error.code == "UPSTREAM_TIMEOUT"
    assert not finished


@pytest.mark.asyncio
async def test_lifespan_defaults_and_closes_http_client(monkeypatch):
    monkeypatch.delenv("MCP_FACADE_BASE_URL", raising=False)
    async with mcp_server._lifespan(mcp_server.server) as client:
        assert str(client.base_url) == "http://127.0.0.1:8000"
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.asyncio
async def test_stdio_discovers_tools_offline_and_invokes_against_stub():
    requests = []

    async def handle(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            requests.append(request.splitlines()[0])
            if len(requests) == 1:
                status = b"200 OK"
                body = json.dumps(CAPITAL).encode()
            else:
                status = b"502 Bad Gateway"
                body = b'{"error":{"code":"soap_fault","message":"private upstream details"}}'
            writer.write(
                b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    stub = await asyncio.start_server(handle, "127.0.0.1", 0, start_serving=False)
    async with stub:
        port = stub.sockets[0].getsockname()[1]
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "soap_rest_ai.mcp_server"],
            cwd=Path(__file__).resolve().parents[1],
            env={"MCP_FACADE_BASE_URL": f"http://127.0.0.1:{port}", "NO_PROXY": "127.0.0.1"},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert set(tools) == {case[0] for case in CASES}

                invalid = await session.call_tool(CAPITAL_TOOL, {"country_code": 63})
                assert invalid.isError is True
                assert invalid.structuredContent["data"] is None
                assert invalid.structuredContent["error"]["code"] == "INVALID_INPUT"
                assert json.loads(invalid.content[0].text) == invalid.structuredContent
                validate(invalid.structuredContent, tools[CAPITAL_TOOL].outputSchema)

                await stub.start_serving()
                result = await session.call_tool(CAPITAL_TOOL, {"country_code": " ph "})
                assert result.isError is False
                assert result.structuredContent == {
                    "success": True, "operation": "CapitalCity",
                    "input": {"country_code": "PH"}, "data": "Manila",
                }
                assert json.loads(result.content[0].text) == result.structuredContent

                fault = await session.call_tool(CAPITAL_TOOL, {"country_code": "PH"})
                assert fault.isError is True
                assert fault.structuredContent["error"]["code"] == "UPSTREAM_FAULT"
                assert fault.structuredContent["data"] is None
                validate(fault.structuredContent, tools[CAPITAL_TOOL].outputSchema)
                assert json.loads(fault.content[0].text) == fault.structuredContent
                assert "private upstream details" not in fault.content[0].text
                assert requests == [b"GET /countries/PH/capital HTTP/1.1"] * 2
