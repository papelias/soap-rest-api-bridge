"""Shared operation-to-REST mappings for facade clients."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RestEndpoint:
    parameter_name: str
    path_template: str
    code_length: int
    response_code_field: str
    result_field: str


REST_ENDPOINTS = {
    "CapitalCity": RestEndpoint(
        parameter_name="sCountryISOCode",
        path_template="/countries/{code}/capital",
        code_length=2,
        response_code_field="iso_code",
        result_field="capital",
    ),
    "CountryCurrency": RestEndpoint(
        parameter_name="sCountryISOCode",
        path_template="/countries/{code}/currency",
        code_length=2,
        response_code_field="iso_code",
        result_field="currency",
    ),
    "CountryIntPhoneCode": RestEndpoint(
        parameter_name="sCountryISOCode",
        path_template="/countries/{code}/phone-code",
        code_length=2,
        response_code_field="iso_code",
        result_field="phone_code",
    ),
    "FullCountryInfo": RestEndpoint(
        parameter_name="sCountryISOCode",
        path_template="/countries/{code}",
        code_length=2,
        response_code_field="iso_code",
        result_field="country",
    ),
    "CountriesUsingCurrency": RestEndpoint(
        parameter_name="sISOCurrencyCode",
        path_template="/currencies/{code}/countries",
        code_length=3,
        response_code_field="currency_code",
        result_field="countries",
    ),
}
