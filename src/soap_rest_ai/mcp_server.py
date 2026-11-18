"""Expose the five REST facade lookups over MCP stdio."""

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, TypeAdapter

from soap_rest_ai.api.models import (
    CapitalResponse,
    CountryInfoResponse,
    CurrencyCountriesResponse,
    CurrencyResponse,
    PhoneCodeResponse,
)
from soap_rest_ai.codes import normalize_code
from soap_rest_ai.rest_endpoints import REST_ENDPOINTS

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
ATTEMPT_TIMEOUT = 3.0
CALL_TIMEOUT = 7.0
RETRY_DELAY = 0.25
HTTP_DATE = re.compile(
    r"[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
    r"|[A-Z][a-z]+, [0-9]{2}-[A-Z][a-z]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
    r"|[A-Z][a-z]{2} [A-Z][a-z]{2} (?: [0-9]|[0-9]{2}) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}"
)


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    OPERATION_BLOCKED = "OPERATION_BLOCKED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_FAULT = "UPSTREAM_FAULT"
    INVALID_UPSTREAM_RESPONSE = "INVALID_UPSTREAM_RESPONSE"
    FACADE_ERROR = "FACADE_ERROR"


ERROR_MESSAGES = {
    ErrorCode.INVALID_INPUT: "The lookup code has an invalid format.",
    ErrorCode.OPERATION_BLOCKED: "The facade blocked this operation.",
    ErrorCode.UPSTREAM_TIMEOUT: "The lookup timed out.",
    ErrorCode.UPSTREAM_UNAVAILABLE: "The lookup service is unavailable.",
    ErrorCode.UPSTREAM_RATE_LIMITED: "The lookup service is rate limited.",
    ErrorCode.UPSTREAM_FAULT: "The SOAP provider could not complete the lookup.",
    ErrorCode.INVALID_UPSTREAM_RESPONSE: "The lookup returned an invalid response.",
    ErrorCode.FACADE_ERROR: "The facade could not complete the lookup.",
}
FACADE_ERRORS = {
    "invalid_input": ErrorCode.INVALID_INPUT,
    "operation_blocked": ErrorCode.OPERATION_BLOCKED,
    "upstream_timeout": ErrorCode.UPSTREAM_TIMEOUT,
    "upstream_unavailable": ErrorCode.UPSTREAM_UNAVAILABLE,
    "soap_fault": ErrorCode.UPSTREAM_FAULT,
    "invalid_upstream_response": ErrorCode.INVALID_UPSTREAM_RESPONSE,
    "validation_serialization": ErrorCode.INVALID_UPSTREAM_RESPONSE,
    "internal_error": ErrorCode.FACADE_ERROR,
    "http_error": ErrorCode.FACADE_ERROR,
}
GATEWAY_ERRORS = {
    502: ErrorCode.UPSTREAM_UNAVAILABLE,
    503: ErrorCode.UPSTREAM_UNAVAILABLE,
    504: ErrorCode.UPSTREAM_TIMEOUT,
}

Data = TypeVar("Data")


class Success(BaseModel, Generic[Data]):
    success: Literal[True]
    operation: str
    input: dict[str, str]
    data: Data


class ToolError(BaseModel):
    code: ErrorCode
    message: str


class Failure(BaseModel):
    success: Literal[False]
    operation: str
    input: dict[str, str] | None
    data: None
    error: ToolError


@dataclass(frozen=True)
class Lookup:
    operation: str
    description: str
    response_model: type[BaseModel]

    @property
    def argument(self) -> str:
        return "currency_code" if self.operation == "CountriesUsingCurrency" else "country_code"

    def tool(self, name: str) -> Tool:
        endpoint = REST_ENDPOINTS[self.operation]
        data_type = self.response_model.model_fields[endpoint.result_field].annotation
        output_schema = TypeAdapter(Success[data_type] | Failure).json_schema(mode="serialization")
        return Tool(
            name=name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    self.argument: {
                        "type": "string",
                        "description": f"Code with {endpoint.code_length} ASCII letters.",
                    },
                },
                "required": [self.argument],
                "additionalProperties": False,
            },
            outputSchema={"type": "object", **output_schema},
        )


LOOKUPS = {
    "get_country_capital": Lookup("CapitalCity", "Get a country's capital.", CapitalResponse),
    "get_country_currency": Lookup("CountryCurrency", "Get a country's currency.", CurrencyResponse),
    "get_country_phone_code": Lookup(
        "CountryIntPhoneCode", "Get a country's international phone code.", PhoneCodeResponse,
    ),
    "get_country_info": Lookup(
        "FullCountryInfo", "Get full country information.", CountryInfoResponse,
    ),
    "get_countries_by_currency": Lookup(
        "CountriesUsingCurrency", "List countries using a currency.", CurrencyCountriesResponse,
    ),
}


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    if value.isascii() and value.isdecimal():
        return max(RETRY_DELAY, float(value))
    # The email date parser also accepts non-HTTP formats and ignores trailing text.
    if not HTTP_DATE.fullmatch(value):
        return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(RETRY_DELAY, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, OverflowError):
        return None


def _response_error(response: httpx.Response) -> tuple[ErrorCode, float | None]:
    try:
        body = response.json()
    except (ValueError, RecursionError):
        body = None
    metadata = body.get("error") if isinstance(body, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    facade_code = metadata.get("code")
    known_code = FACADE_ERRORS.get(facade_code) if isinstance(facade_code, str) else None
    status = response.status_code

    if status == 429 or metadata.get("upstream_status") == 429:
        delay = _retry_after(response)
        if status != 429 and known_code and metadata.get("retryable") is False:
            delay = None
        return ErrorCode.UPSTREAM_RATE_LIMITED, delay
    if known_code is not None:
        retry = (
            status in GATEWAY_ERRORS
            and known_code in {ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE}
            and metadata.get("retryable") is True
        )
        return known_code, RETRY_DELAY if retry else None
    if status in GATEWAY_ERRORS:
        return GATEWAY_ERRORS[status], RETRY_DELAY
    if status == 403:
        return ErrorCode.OPERATION_BLOCKED, None
    if status == 422:
        return ErrorCode.INVALID_INPUT, None
    if response.is_success:
        return ErrorCode.INVALID_UPSTREAM_RESPONSE, None
    return ErrorCode.FACADE_ERROR, None


def _failure(lookup: Lookup, code: str | None, error: ErrorCode) -> Failure:
    return Failure(
        success=False,
        operation=lookup.operation,
        input={lookup.argument: code} if code is not None else None,
        data=None,
        error=ToolError(code=error, message=ERROR_MESSAGES[error]),
    )


def _success(response: httpx.Response, lookup: Lookup, code: str) -> Success[Any] | Failure:
    endpoint = REST_ENDPOINTS[lookup.operation]
    try:
        # The REST response uses field names even where SOAP uses validation aliases.
        result = lookup.response_model.model_validate_json(
            response.content, strict=True, by_alias=False, by_name=True,
        )
        if "operation" not in result.model_fields_set or getattr(result, endpoint.response_code_field) != code:
            raise ValueError("Mismatched REST response")
    except ValueError:
        return _failure(lookup, code, ErrorCode.INVALID_UPSTREAM_RESPONSE)
    return Success(
        success=True, operation=lookup.operation, input={lookup.argument: code},
        data=getattr(result, endpoint.result_field),
    )


async def run_lookup(
    client: httpx.AsyncClient, lookup: Lookup, arguments: dict[str, Any]
) -> Success[Any] | Failure:
    endpoint = REST_ENDPOINTS[lookup.operation]
    try:
        if arguments.keys() != {lookup.argument}:
            raise ValueError("Unexpected arguments")
        code = normalize_code(arguments[lookup.argument], endpoint.code_length)
    except ValueError:
        return _failure(lookup, None, ErrorCode.INVALID_INPUT)

    path = endpoint.path_template.format(code=code)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CALL_TIMEOUT
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(2):
                try:
                    async with asyncio.timeout(ATTEMPT_TIMEOUT):
                        response = await client.get(path)
                except (TimeoutError, httpx.TimeoutException):
                    error, delay = ErrorCode.UPSTREAM_TIMEOUT, RETRY_DELAY
                except (httpx.NetworkError, httpx.RemoteProtocolError):
                    error, delay = ErrorCode.UPSTREAM_UNAVAILABLE, RETRY_DELAY
                except httpx.DecodingError:
                    return _failure(lookup, code, ErrorCode.INVALID_UPSTREAM_RESPONSE)
                except (httpx.RequestError, httpx.InvalidURL):
                    return _failure(lookup, code, ErrorCode.FACADE_ERROR)
                else:
                    if response.status_code == 200:
                        return _success(response, lookup, code)
                    error, delay = _response_error(response)
                if attempt == 1 or delay is None or loop.time() + delay >= deadline:
                    return _failure(lookup, code, error)
                await asyncio.sleep(delay)
    except TimeoutError:
        return _failure(lookup, code, ErrorCode.UPSTREAM_TIMEOUT)


@asynccontextmanager
async def _lifespan(_: Server) -> AsyncIterator[httpx.AsyncClient]:
    base_url = os.environ.get("MCP_FACADE_BASE_URL", DEFAULT_BASE_URL)
    async with httpx.AsyncClient(base_url=base_url, timeout=ATTEMPT_TIMEOUT) as client:
        yield client


server = Server("soap-rest-ai-bridge", lifespan=_lifespan)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [lookup.tool(name) for name, lookup in LOOKUPS.items()]


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    lookup = LOOKUPS.get(name)
    if lookup is None:
        return CallToolResult(isError=True, content=[TextContent(type="text", text="Unknown tool.")])

    result = await run_lookup(server.request_context.lifespan_context, lookup, arguments)
    # Explicit results carry the error flag and the same envelope in both content formats.
    return CallToolResult(
        isError=not result.success,
        structuredContent=result.model_dump(mode="json"),
        content=[TextContent(type="text", text=result.model_dump_json())],
    )


async def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
