"""Async CountryInfoService adapter; Zeep objects stay inside this module."""

import asyncio
import json
from types import TracebackType
from typing import Self, TypeAlias

import httpx
from zeep import AsyncClient
from zeep.exceptions import Error as ZeepError
from zeep.exceptions import Fault, TransportError, XMLSyntaxError
from zeep.helpers import serialize_object
from zeep.loader import parse_xml
from zeep.transports import AsyncTransport

from soap_rest_ai.config import Settings
from soap_rest_ai.codes import normalize_code
from soap_rest_ai.soap.errors import SoapErrorKind, SoapIntegrationError
from soap_rest_ai.soap.wsdl import https_url, load_wsdl, wsdl_url, xml_settings

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}
_STANDARD_FAULT_CODES = {
    "VersionMismatch", "MustUnderstand", "Client", "Server",
    "DataEncodingUnknown", "Sender", "Receiver",
}


class _SoapTransport(AsyncTransport):
    """Preserve real SOAP faults while classifying HTTP error pages correctly."""

    def __init__(
        self, http: httpx.AsyncClient, wsdl_origin: httpx.URL | None,
        endpoint: httpx.URL | None,
    ) -> None:
        # WSDL loading is handled separately. This transport only sends SOAP requests.
        self._close_session = False
        self.client = http
        self._wsdl_origin = wsdl_origin
        self._endpoint = endpoint

    def load(self, url: str) -> bytes:
        raise ValueError("External SOAP response references are disabled.")

    async def get(self, address: str, params: dict, headers: dict) -> httpx.Response:
        raise ValueError("The country information adapter requires a SOAP binding.")

    async def post(
        self, address: str, message: bytes, headers: dict[str, str]
    ) -> httpx.Response:
        url = https_url(address)
        if self._endpoint is not None:
            allowed = url == self._endpoint
        else:
            allowed = self._wsdl_origin is not None and url.netloc == self._wsdl_origin.netloc
        if not allowed:
            raise ValueError("The SOAP endpoint is outside the configured destination.")
        response = await self.client.post(url, content=message, headers=headers, follow_redirects=False)
        if response.is_redirect:
            response.raise_for_status()
        if response.is_error:
            try:
                envelope = parse_xml(response.content, self, settings=xml_settings())
            except XMLSyntaxError:
                response.raise_for_status()
            else:
                namespaces = (
                    "http://schemas.xmlsoap.org/soap/envelope/",
                    "http://www.w3.org/2003/05/soap-envelope",
                )
                if not any(
                    envelope.find(f"{{{ns}}}Body/{{{ns}}}Fault") is not None
                    for ns in namespaces
                ):
                    response.raise_for_status()
        return response


def _translate_error(exc: Exception, operation: str) -> SoapIntegrationError:
    if isinstance(exc, Fault):
        code = str(exc.code).rsplit(":", 1)[-1]
        return SoapIntegrationError(
            SoapErrorKind.SOAP_FAULT, operation, "The SOAP service could not complete the request.",
            fault_code=f"soap:{code}" if code in _STANDARD_FAULT_CODES else None,
        )
    if isinstance(exc, httpx.TimeoutException):
        return SoapIntegrationError(
            SoapErrorKind.UPSTREAM_TIMEOUT, operation, "SOAP upstream timed out.",
            retryable=True,
        )
    status = None
    if isinstance(exc, TransportError):
        status = exc.status_code
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    if status and status >= 400:
        return SoapIntegrationError(
            SoapErrorKind.UPSTREAM_UNAVAILABLE, operation,
            f"SOAP upstream returned HTTP {status}.", http_status=status,
            retryable=status in _TRANSIENT_STATUSES,
        )
    if isinstance(exc, (httpx.RequestError, OSError)):
        return SoapIntegrationError(
            SoapErrorKind.UPSTREAM_UNAVAILABLE, operation,
            "SOAP upstream could not be reached.",
            retryable=isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError)),
        )
    return SoapIntegrationError(
        SoapErrorKind.VALIDATION_SERIALIZATION, operation,
        "SOAP configuration, request, or response could not be validated or serialized.",
    )


class CountryInfoAdapter:
    """Use as an async context manager; the first valid call loads the WSDL."""

    def __init__(
        self, settings: Settings, *, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(trust_env=False)
        self._http.timeout = httpx.Timeout(settings.soap_timeout_seconds)
        self._owns_http = http_client is None
        self._client: AsyncClient | None = None
        self._init_lock = asyncio.Lock()
        self._closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        self._closed = True

    async def _create_client(self) -> AsyncClient:
        source = self._settings.soap_wsdl_url
        remote = wsdl_url(source)
        configured_endpoint = self._settings.soap_endpoint_url
        endpoint = https_url(configured_endpoint) if configured_endpoint else None
        if remote is None and endpoint is None:
            raise ValueError("A local WSDL requires an explicitly configured SOAP endpoint.")
        document = await load_wsdl(source, self._http)
        return AsyncClient(
            document, transport=_SoapTransport(self._http, remote, endpoint),
            settings=document.settings,
        )

    async def _get_client(self) -> AsyncClient:
        async with self._init_lock:
            if self._client is None:
                self._client = await self._create_client()
            return self._client

    async def _call(
        self, operation: str, parameter: str, value: str, code_length: int
    ) -> JsonValue:
        try:
            code = normalize_code(value, code_length)
        except ValueError:
            raise SoapIntegrationError(
                SoapErrorKind.VALIDATION_SERIALIZATION, operation,
                f"{parameter} must contain {code_length} ASCII letters.",
            ) from None
        if self._closed or not self._settings.soap_wsdl_url.strip():
            raise SoapIntegrationError(
                SoapErrorKind.VALIDATION_SERIALIZATION, operation,
                "Adapter is closed or SOAP_WSDL_URL is not configured.",
            )

        for attempt in range(self._settings.soap_max_retries + 1):
            try:
                client = await self._get_client()
                result = await client.service[operation](**{parameter: code})
                native = serialize_object(result, target_cls=dict)
                # Reject unsupported types and non-finite floats instead of leaking them.
                return json.loads(json.dumps(native, allow_nan=False))
            except (
                ZeepError, httpx.HTTPError, httpx.InvalidURL, OSError, TypeError, ValueError, AttributeError
            ) as exc:
                error = _translate_error(exc, operation)
                if not error.retryable or attempt == self._settings.soap_max_retries:
                    raise error from None

        raise RuntimeError("SOAP retry loop completed without a result")

    async def capital_city(self, country_code: str) -> JsonValue:
        return await self._call("CapitalCity", "sCountryISOCode", country_code, 2)

    async def country_currency(self, country_code: str) -> JsonValue:
        return await self._call("CountryCurrency", "sCountryISOCode", country_code, 2)

    async def country_int_phone_code(self, country_code: str) -> JsonValue:
        return await self._call("CountryIntPhoneCode", "sCountryISOCode", country_code, 2)

    async def full_country_info(self, country_code: str) -> JsonValue:
        return await self._call("FullCountryInfo", "sCountryISOCode", country_code, 2)

    async def countries_using_currency(self, currency_code: str) -> JsonValue:
        return await self._call("CountriesUsingCurrency", "sISOCurrencyCode", currency_code, 3)
