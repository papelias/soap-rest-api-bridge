"""Offline SOAP parsing, normalization, validation, and transport failure tests."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from xml.etree import ElementTree

import httpx
import pytest
from fastapi import FastAPI

from soap_rest_ai.api.errors import register_error_handlers
from soap_rest_ai.api.facade import router as facade_router
from soap_rest_ai.config import Settings
from soap_rest_ai.soap import client as client_module
from soap_rest_ai.soap import validate_live
from soap_rest_ai.soap.catalog import load_catalog
from soap_rest_ai.soap.client import CountryInfoAdapter
from soap_rest_ai.soap.errors import SoapErrorKind, SoapIntegrationError

FIXTURES = Path(__file__).parent / "fixtures"
NAMESPACE = "{http://www.oorsprong.org/websamples.countryinfo}"
SOAP_HOST = "webservices.oorsprong.org"
SOAP_URL = f"https://{SOAP_HOST}/websamples.countryinfo/CountryInfoService.wso"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    snapshot = (FIXTURES / "countryinfo.wsdl").read_bytes()
    wsdl = tmp_path / "countryinfo.wsdl"
    wsdl.write_bytes(snapshot.replace(f"http://{SOAP_HOST}".encode(), f"https://{SOAP_HOST}".encode()))
    return Settings(
        _env_file=None,
        soap_wsdl_url=str(wsdl),
        soap_endpoint_url=SOAP_URL,
        soap_timeout_seconds=2,
        soap_max_retries=2,
    )


def response(fixture: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=(FIXTURES / "soap" / fixture).read_bytes(),
        headers={"Content-Type": "text/xml; charset=utf-8"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "code", "fixture", "expected"),
    [
        ("capital_city", " ph ", "capital_city.xml", "Manila"),
        ("country_int_phone_code", "PH", "country_int_phone_code.xml", "63"),
        (
            "country_currency", "PH", "country_currency.xml",
            {"sISOCode": "PHP", "sName": "Pesos"},
        ),
        (
            "countries_using_currency", "php", "countries_using_currency.xml",
            [
                {"sISOCode": "PH", "sName": "Philippines"},
                {"sISOCode": "ZZ", "sName": "Fixture country"},
            ],
        ),
        (
            "full_country_info", "PH", "full_country_info.xml",
            {
                "sISOCode": "PH", "sName": "Philippines", "sCapitalCity": "Manila",
                "sPhoneCode": "63", "sContinentCode": "AS", "sCurrencyISOCode": "PHP",
                "sCountryFlag": "http://www.oorsprong.org/WebSamples.CountryInfo/Flags/Philippines.jpg",
                "Languages": {"tLanguage": [
                    {"sISOCode": "fil", "sName": "Filipino"},
                    {"sISOCode": "eng", "sName": "English"},
                ]},
            },
        ),
    ],
)
async def test_parse_and_normalize_raw_xml(
    settings: Settings, method: str, code: str, fixture: str, expected: Any
) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(fixture)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            result = await getattr(adapter, method)(code)
        assert not http.is_closed  # Injected clients remain owned by the caller.

    assert result == expected
    assert json.loads(json.dumps(result, allow_nan=False)) == expected
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == SOAP_URL
    body = ElementTree.fromstring(request.content).find(
        "{http://schemas.xmlsoap.org/soap/envelope/}Body"
    )
    operation = next(iter(body))
    assert operation.tag == NAMESPACE + {
        "capital_city": "CapitalCity",
        "country_currency": "CountryCurrency",
        "country_int_phone_code": "CountryIntPhoneCode",
        "full_country_info": "FullCountryInfo",
        "countries_using_currency": "CountriesUsingCurrency",
    }[method]
    parameter = next(iter(operation))
    name = "sISOCurrencyCode" if method == "countries_using_currency" else "sCountryISOCode"
    assert parameter.tag == NAMESPACE + name
    assert parameter.text == code.strip().upper()
    assert set(request.extensions["timeout"].values()) == {2}


@pytest.mark.asyncio
async def test_fault_is_structured_and_never_retried(settings: Settings) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response("fault.xml", status=500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == 1
    assert error.value.to_dict() == {
        "kind": "soap_fault",
        "operation": "CapitalCity",
        "message": "The SOAP service could not complete the request.",
        "retryable": False,
        "fault_code": "soap:Server",
        "http_status": None,
    }
    json.dumps(error.value.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "P", "PHL", "12", "éé", None, 123])
async def test_invalid_country_code_does_not_load_wsdl_or_retry(
    settings: Settings, value: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(side_effect=AssertionError("WSDL must not load"))
    async with CountryInfoAdapter(settings) as adapter:
        monkeypatch.setattr(adapter, "_create_client", create)
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.capital_city(value)

    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    assert not error.value.retryable
    create.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_currency_code_is_not_sent(settings: Settings) -> None:
    async with CountryInfoAdapter(settings) as adapter:
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.countries_using_currency("PH")
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    assert error.value.operation == "CountriesUsingCurrency"


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 2])
@pytest.mark.parametrize(
    ("exception", "kind"),
    [
        (httpx.ReadTimeout, SoapErrorKind.UPSTREAM_TIMEOUT),
        (httpx.ConnectTimeout, SoapErrorKind.UPSTREAM_TIMEOUT),
        (httpx.ConnectError, SoapErrorKind.UPSTREAM_UNAVAILABLE),
        (httpx.RemoteProtocolError, SoapErrorKind.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_transient_transport_errors_respect_retry_limit(
    settings: Settings, retries: int, exception: type[httpx.RequestError], kind: SoapErrorKind
) -> None:
    settings.soap_max_retries = retries
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise exception("Mock upstream failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == retries + 1
    assert error.value.kind == kind
    assert error.value.retryable
    assert error.value.operation == "CapitalCity"


@pytest.mark.asyncio
async def test_transient_failure_can_recover(settings: Settings) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("Mock timeout", request=request)
        return response("capital_city.xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            assert await adapter.capital_city("PH") == "Manila"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "attempts"), [(503, 3), (429, 3), (404, 1), (401, 1)])
@pytest.mark.parametrize("body", ["Mock unavailable service", "<html><body>Unavailable</body></html>"])
async def test_http_errors_are_structured_and_selectively_retried(
    settings: Settings, status: int, attempts: int, body: str
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == attempts
    assert error.value.kind == SoapErrorKind.UPSTREAM_UNAVAILABLE
    assert error.value.http_status == status
    assert error.value.retryable == (attempts > 1)


@pytest.mark.asyncio
async def test_malformed_response_is_not_retried(settings: Settings) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"<not-valid-xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == 1
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    assert not error.value.retryable


@pytest.mark.asyncio
async def test_unsupported_result_is_not_leaked_or_retried(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response("capital_city.xml")

    monkeypatch.setattr(client_module, "serialize_object", lambda *args, **kwargs: object())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == 1
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    json.dumps(error.value.to_dict())


@pytest.mark.asyncio
async def test_missing_wsdl_url_is_configuration_validation(settings: Settings) -> None:
    settings.soap_wsdl_url = ""
    async with CountryInfoAdapter(settings) as adapter:
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.capital_city("PH")
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [True, False])
async def test_wsdl_transport_errors_use_settings_and_are_structured(
    settings: Settings, timeout: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.soap_wsdl_url = "https://example.test/service?WSDL"
    calls = 0

    async def handle(transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert set(request.extensions["timeout"].values()) == {2}
        if timeout:
            raise httpx.ReadTimeout("Mock WSDL timeout", request=request)
        return httpx.Response(503)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    async with CountryInfoAdapter(settings) as adapter:
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.capital_city("PH")

    assert calls == 3
    expected = SoapErrorKind.UPSTREAM_TIMEOUT if timeout else SoapErrorKind.UPSTREAM_UNAVAILABLE
    assert error.value.kind == expected
    assert error.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("use_catalog", [False, True])
@pytest.mark.parametrize("payload", ["redirect", "remote import", "file import", "xsd include", "doctype"])
async def test_untrusted_wsdl_cannot_trigger_secondary_requests(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, use_catalog: bool, payload: str
) -> None:
    settings.soap_wsdl_url = f"{SOAP_URL}?WSDL"
    requests = []

    async def handle(transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if payload == "redirect":
            return httpx.Response(302, headers={"Location": "https://unapproved.test/wsdl"})
        if payload == "doctype":
            xml = '<!DOCTYPE definitions><definitions xmlns="http://schemas.xmlsoap.org/wsdl/"/>'
        elif payload == "xsd include":
            xml = (
                '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/">'
                '<types><schema xmlns="http://www.w3.org/2001/XMLSchema">'
                '<include schemaLocation="https://unapproved.test/schema.xsd"/>'
                '</schema></types></definitions>'
            )
        else:
            location = (
                "https://unapproved.test/schema.xsd" if payload == "remote import"
                else "file:///secret.xsd"
            )
            xml = (
                '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/">'
                f'<import namespace="urn:test" location="{location}"/>'
                '</definitions>'
            )
        return httpx.Response(200, text=xml)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    if use_catalog:
        with pytest.raises((httpx.HTTPStatusError, client_module.ZeepError, ValueError)):
            await load_catalog(settings.soap_wsdl_url, settings.soap_timeout_seconds)
    else:
        async with CountryInfoAdapter(settings) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")
        assert not error.value.retryable
    assert requests == [settings.soap_wsdl_url]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["http://example.test/service?WSDL", "file:///secret.wsdl"])
async def test_unsafe_wsdl_sources_are_rejected(settings: Settings, source: str) -> None:
    settings.soap_wsdl_url = source
    with pytest.raises(ValueError, match="must use HTTPS"):
        await load_catalog(source, settings.soap_timeout_seconds)
    async with CountryInfoAdapter(settings) as adapter:
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.capital_city("PH")
    assert not error.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address", ["https://unapproved.test/service", SOAP_URL + "/other", SOAP_URL.replace("https:", "http:")]
)
async def test_soap_address_must_match_configured_endpoint(settings: Settings, address: str) -> None:
    wsdl = Path(settings.soap_wsdl_url)
    wsdl.write_bytes(wsdl.read_bytes().replace(SOAP_URL.encode(), address.encode()))
    async with CountryInfoAdapter(settings) as adapter:
        with pytest.raises(SoapIntegrationError) as error:
            await adapter.capital_city("PH")
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    assert not error.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_origin", [False, True])
async def test_remote_wsdl_pins_the_soap_origin(
    settings: Settings, foreign_origin: bool
) -> None:
    settings.soap_wsdl_url = f"{SOAP_URL}?WSDL"
    settings.soap_endpoint_url = ""
    snapshot = (FIXTURES / "countryinfo.wsdl").read_bytes()
    if foreign_origin:
        snapshot = snapshot.replace(SOAP_HOST.encode(), b"unapproved.test")
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(200, content=snapshot)
        return response("capital_city.xml")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            if foreign_origin:
                with pytest.raises(SoapIntegrationError):
                    await adapter.capital_city("PH")
            else:
                assert await adapter.capital_city("PH") == "Manila"
    assert requests == ([settings.soap_wsdl_url] if foreign_origin else [settings.soap_wsdl_url, SOAP_URL])


@pytest.mark.asyncio
async def test_soap_redirect_is_never_followed(settings: Settings) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://unapproved.test/soap"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert not error.value.retryable
    assert requests == [SOAP_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_soap_doctype_is_rejected_without_retry(settings: Settings, status: int) -> None:
    result = response("fault.xml" if status == 500 else "capital_city.xml", status)
    doctype = b'<!DOCTYPE Envelope SYSTEM "https://unapproved.test/external.dtd">'
    declaration, body = result.content.split(b"?>", 1)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, content=declaration + b"?>" + doctype + body, headers=result.headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            with pytest.raises(SoapIntegrationError) as error:
                await adapter.capital_city("PH")

    assert calls == 1
    assert error.value.kind == SoapErrorKind.VALIDATION_SERIALIZATION
    assert not error.value.retryable


@pytest.mark.asyncio
async def test_upstream_fault_text_and_custom_code_stay_out_of_rest(settings: Settings) -> None:
    original = response("fault.xml", 500)
    content = original.content.replace(b"soap:Server", b"private-hostname")
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(facade_router)
    app.state.settings = settings

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=content, headers=original.headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            app.state.soap_adapter = adapter
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                result = await api.get("/countries/PH/capital")

    assert result.status_code == 502
    error = result.json()["error"]
    assert error["message"] == "The SOAP service could not complete the request."
    assert error["fault_code"] is None
    assert "private-hostname" not in result.text
    assert "Representative test fault" not in result.text


@pytest.mark.asyncio
async def test_wsdl_load_is_lazy_and_reused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response("capital_city.xml"))
    ) as http:
        async with CountryInfoAdapter(settings, http_client=http) as adapter:
            create = AsyncMock(wraps=adapter._create_client)
            monkeypatch.setattr(adapter, "_create_client", create)
            create.assert_not_called()
            assert await adapter.capital_city("PH") == "Manila"
            assert await adapter.capital_city("PH") == "Manila"
            create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_manual_validation_checks_all_five_operations(
    failure: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter = MagicMock()
    adapter.__aenter__ = AsyncMock(return_value=adapter)
    adapter.__aexit__ = AsyncMock(return_value=None)
    adapter.capital_city = AsyncMock(return_value="Manila")
    adapter.country_currency = AsyncMock(return_value={"sISOCode": "PHP"})
    adapter.country_int_phone_code = AsyncMock(return_value="63")
    adapter.full_country_info = AsyncMock(return_value={"sISOCode": "PH"})
    adapter.countries_using_currency = AsyncMock(return_value=[{"sISOCode": "PH"}])
    if failure:
        adapter.capital_city.side_effect = SoapIntegrationError(
            SoapErrorKind.UPSTREAM_TIMEOUT, "CapitalCity", "Mock timeout", retryable=True
        )
    monkeypatch.setattr(validate_live, "CountryInfoAdapter", lambda settings: adapter)

    assert await validate_live.validate() is (not failure)
    assert capsys.readouterr().out.count('"ok":') == 5
    adapter.capital_city.assert_awaited_once_with("PH")
    adapter.country_currency.assert_awaited_once_with("PH")
    adapter.country_int_phone_code.assert_awaited_once_with("PH")
    adapter.full_country_info.assert_awaited_once_with("PH")
    adapter.countries_using_currency.assert_awaited_once_with("PHP")
