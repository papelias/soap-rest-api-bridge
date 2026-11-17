"""Thin REST routes with explicit SOAP operation traceability and policy guards."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from soap_rest_ai.api.dependencies import get_adapter, get_settings
from soap_rest_ai.api.models import (
    CapitalResponse, CountryCode, CountryInfoResponse, CurrencyCode,
    CurrencyCountriesResponse, CurrencyResponse, ErrorResponse, PhoneCodeResponse,
)
from soap_rest_ai.config import Settings
from soap_rest_ai.policy.operations import require_operation
from soap_rest_ai.soap.client import CountryInfoAdapter

router = APIRouter(
    tags=["Country information"],
    responses={
        403: {"model": ErrorResponse, "description": "SOAP operation blocked by execution policy."},
        422: {"model": ErrorResponse, "description": "Invalid country or currency code."},
        500: {"model": ErrorResponse, "description": "Unexpected server error."},
        502: {"model": ErrorResponse, "description": "SOAP fault or invalid upstream result."},
        503: {"model": ErrorResponse, "description": "SOAP upstream unavailable."},
        504: {"model": ErrorResponse, "description": "SOAP upstream timeout."},
    },
)
Adapter = Annotated[CountryInfoAdapter, Depends(get_adapter)]
Configuration = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/countries/{iso_code}/capital", response_model=CapitalResponse,
    operation_id="CapitalCity", summary="Get a country's capital",
    description="Invokes the approved SOAP operation CapitalCity.",
)
async def capital(iso_code: CountryCode, adapter: Adapter, settings: Configuration) -> dict[str, Any]:
    require_operation("CapitalCity", settings.allowed_operations)
    return {"iso_code": iso_code, "capital": await adapter.capital_city(iso_code)}


@router.get(
    "/countries/{iso_code}/currency", response_model=CurrencyResponse,
    operation_id="CountryCurrency", summary="Get a country's currency",
    description="Invokes the approved SOAP operation CountryCurrency.",
)
async def currency(iso_code: CountryCode, adapter: Adapter, settings: Configuration) -> dict[str, Any]:
    require_operation("CountryCurrency", settings.allowed_operations)
    return {"iso_code": iso_code, "currency": await adapter.country_currency(iso_code)}


@router.get(
    "/countries/{iso_code}/phone-code", response_model=PhoneCodeResponse,
    operation_id="CountryIntPhoneCode", summary="Get a country's international phone code",
    description="Invokes the approved SOAP operation CountryIntPhoneCode.",
)
async def phone_code(iso_code: CountryCode, adapter: Adapter, settings: Configuration) -> dict[str, Any]:
    require_operation("CountryIntPhoneCode", settings.allowed_operations)
    return {"iso_code": iso_code, "phone_code": await adapter.country_int_phone_code(iso_code)}


@router.get(
    "/countries/{iso_code}", response_model=CountryInfoResponse,
    operation_id="FullCountryInfo", summary="Get full country information",
    description="Invokes the approved SOAP operation FullCountryInfo.",
)
async def country(iso_code: CountryCode, adapter: Adapter, settings: Configuration) -> dict[str, Any]:
    require_operation("FullCountryInfo", settings.allowed_operations)
    return {"iso_code": iso_code, "country": await adapter.full_country_info(iso_code)}


@router.get(
    "/currencies/{currency_code}/countries", response_model=CurrencyCountriesResponse,
    operation_id="CountriesUsingCurrency", summary="List countries using a currency",
    description="Invokes the approved SOAP operation CountriesUsingCurrency.",
)
async def currency_countries(
    currency_code: CurrencyCode, adapter: Adapter, settings: Configuration
) -> dict[str, Any]:
    require_operation("CountriesUsingCurrency", settings.allowed_operations)
    return {"currency_code": currency_code, "countries": await adapter.countries_using_currency(currency_code)}
