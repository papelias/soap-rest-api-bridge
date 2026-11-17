"""Offline REST facade tests with dependency-injected SOAP adapter mocks."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, create_autospec

import httpx
import pytest
import pytest_asyncio

from soap_rest_ai import main
from soap_rest_ai.api.dependencies import get_adapter, get_settings
from soap_rest_ai.config import Settings
from soap_rest_ai.policy.operations import APPROVED_OPERATIONS
from soap_rest_ai.soap.client import CountryInfoAdapter
from soap_rest_ai.soap.errors import SoapErrorKind, SoapIntegrationError

CASES = [
    ("/countries/%20ph%20/capital", "CapitalCity", "capital_city", "PH", {
        "operation": "CapitalCity", "iso_code": "PH", "capital": "Manila",
    }),
    ("/countries/ph/currency", "CountryCurrency", "country_currency", "PH", {
        "operation": "CountryCurrency", "iso_code": "PH",
        "currency": {"iso_code": "PHP", "name": "Pesos"},
    }),
    ("/countries/ph/phone-code", "CountryIntPhoneCode", "country_int_phone_code", "PH", {
        "operation": "CountryIntPhoneCode", "iso_code": "PH", "phone_code": "63",
    }),
    ("/countries/ph", "FullCountryInfo", "full_country_info", "PH", {
        "operation": "FullCountryInfo", "iso_code": "PH", "country": {
            "iso_code": "PH", "name": "Philippines", "capital": "Manila", "phone_code": "63",
            "continent_code": "AS", "currency_code": "PHP", "flag_url": "https://example.test/ph.jpg",
            "languages": [{"iso_code": "fil", "name": "Filipino"}],
        },
    }),
    ("/currencies/%20php%20/countries", "CountriesUsingCurrency", "countries_using_currency", "PHP", {
        "operation": "CountriesUsingCurrency", "currency_code": "PHP",
        "countries": [{"iso_code": "PH", "name": "Philippines"}],
    }),
]
ERROR_KEYS = {"code", "message", "operation", "retryable", "fault_code", "upstream_status"}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, allowed_operations=APPROVED_OPERATIONS)


@pytest.fixture
def adapter() -> MagicMock:
    mock = create_autospec(CountryInfoAdapter, instance=True)
    mock.capital_city.return_value = "Manila"
    mock.country_currency.return_value = {"sISOCode": "PHP", "sName": "Pesos"}
    mock.country_int_phone_code.return_value = "63"
    mock.full_country_info.return_value = {
        "sISOCode": "PH", "sName": "Philippines", "sCapitalCity": "Manila", "sPhoneCode": "63",
        "sContinentCode": "AS", "sCurrencyISOCode": "PHP", "sCountryFlag": "https://example.test/ph.jpg",
        "Languages": {"tLanguage": [{"sISOCode": "fil", "sName": "Filipino"}]},
    }
    mock.countries_using_currency.return_value = [{"sISOCode": "PH", "sName": "Philippines"}]
    return mock


@pytest_asyncio.fixture
async def api(settings: Settings, adapter: MagicMock) -> AsyncIterator[httpx.AsyncClient]:
    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[get_adapter] = lambda: adapter
    try:
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "operation", "method", "code", "expected"), CASES)
async def test_success_routes_and_operation_traceability(
    api: httpx.AsyncClient, adapter: MagicMock, path: str, operation: str,
    method: str, code: str, expected: dict[str, Any],
) -> None:
    response = await api.get(path)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    getattr(adapter, method).assert_awaited_once_with(code)
    for _, _, other, _, _ in CASES:
        if other != method:
            getattr(adapter, other).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "operation", "method", "code", "expected"), CASES)
async def test_every_route_checks_policy_before_calling_soap(
    api: httpx.AsyncClient, settings: Settings, adapter: MagicMock, path: str,
    operation: str, method: str, code: str, expected: dict[str, Any],
) -> None:
    settings.allowed_operations = tuple(op for op in APPROVED_OPERATIONS if op != operation)
    response = await api.get(path)
    assert response.status_code == 403
    error = response.json()["error"]
    assert set(error) == ERROR_KEYS
    assert error["code"] == "operation_blocked"
    assert error["operation"] == operation
    for _, _, name, _, _ in CASES:
        getattr(adapter, name).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/countries/P/capital", "/countries/PHL/currency", "/countries/12/phone-code",
    "/countries/éé", "/currencies/PH/countries", "/currencies/123/countries",
])
async def test_invalid_codes_return_structured_422_without_soap(
    api: httpx.AsyncClient, adapter: MagicMock, path: str
) -> None:
    response = await api.get(path)
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(error) == ERROR_KEYS
    assert error["code"] == "invalid_input"
    assert error["operation"] in APPROVED_OPERATIONS
    for _, _, name, _, _ in CASES:
        getattr(adapter, name).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "status", "retryable"), [
    (SoapErrorKind.SOAP_FAULT, 502, False),
    (SoapErrorKind.UPSTREAM_TIMEOUT, 504, True),
    (SoapErrorKind.UPSTREAM_UNAVAILABLE, 503, True),
    (SoapErrorKind.VALIDATION_SERIALIZATION, 502, False),
])
async def test_soap_error_status_mapping(
    api: httpx.AsyncClient, adapter: MagicMock,
    kind: SoapErrorKind, status: int, retryable: bool,
) -> None:
    adapter.capital_city.side_effect = SoapIntegrationError(
        kind, "CapitalCity", "Representative failure", retryable=retryable,
        fault_code="soap:Server" if kind == SoapErrorKind.SOAP_FAULT else None,
        http_status=503 if kind == SoapErrorKind.UPSTREAM_UNAVAILABLE else None,
    )
    response = await api.get("/countries/PH/capital")
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == ERROR_KEYS
    assert error["code"] == kind.value
    assert error["operation"] == "CapitalCity"
    assert error["retryable"] == retryable
    assert error["fault_code"] == ("soap:Server" if kind == SoapErrorKind.SOAP_FAULT else None)
    assert error["upstream_status"] == (503 if kind == SoapErrorKind.UPSTREAM_UNAVAILABLE else None)


@pytest.mark.asyncio
async def test_invalid_upstream_result_returns_502(
    api: httpx.AsyncClient, adapter: MagicMock
) -> None:
    adapter.country_currency.return_value = {"unexpected": "record"}
    response = await api.get("/countries/PH/currency")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/soap/CapitalCity"])
async def test_unimplemented_routes_use_the_error_envelope(api: httpx.AsyncClient, path: str) -> None:
    response = await api.get(path)
    assert response.status_code == 404
    assert set(response.json()["error"]) == ERROR_KEYS


@pytest.mark.asyncio
async def test_method_not_allowed_preserves_allow_header(api: httpx.AsyncClient) -> None:
    response = await api.post("/countries/PH/capital")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert set(response.json()["error"]) == ERROR_KEYS


@pytest.mark.asyncio
async def test_unexpected_error_is_structured_without_internal_details(
    api: httpx.AsyncClient, adapter: MagicMock
) -> None:
    adapter.capital_city.side_effect = RuntimeError("Internal implementation details")
    response = await api.get("/countries/PH/capital")
    assert response.status_code == 500
    assert set(response.json()["error"]) == ERROR_KEYS
    assert "Internal implementation details" not in response.text


@pytest.mark.asyncio
async def test_openapi_documents_the_five_operations_and_error_schemas(api: httpx.AsyncClient) -> None:
    schema = (await api.get("/openapi.json")).json()
    routes = [
        ("/countries/{iso_code}/capital", "CapitalCity"),
        ("/countries/{iso_code}/currency", "CountryCurrency"),
        ("/countries/{iso_code}/phone-code", "CountryIntPhoneCode"),
        ("/countries/{iso_code}", "FullCountryInfo"),
        ("/currencies/{currency_code}/countries", "CountriesUsingCurrency"),
    ]
    assert set(schema["paths"]) == {"/health", "/ask", *(path for path, _ in routes)}
    for path, operation in routes:
        route = schema["paths"][path]["get"]
        assert route["operationId"] == operation
        assert operation in route["description"]
        for status in [403, 422, 500, 502, 503, 504]:
            assert route["responses"][str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
    assert "iso_code" in schema["components"]["schemas"]["Currency"]["properties"]


@pytest.mark.asyncio
async def test_lifespan_does_not_load_wsdl_and_closes_adapter(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(side_effect=AssertionError("Startup must not load WSDL"))
    monkeypatch.setattr(main, "Settings", lambda: settings)
    monkeypatch.setattr(CountryInfoAdapter, "_create_client", create)
    async with main.app.router.lifespan_context(main.app):
        soap = main.app.state.soap_adapter
        assert not soap._closed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as api:
            assert (await api.get("/health")).json() == {"status": "ok"}
    assert soap._closed
    create.assert_not_called()
