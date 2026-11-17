"""Explicit operation-to-REST mapping with deterministic parameter validation."""

from dataclasses import dataclass

from soap_rest_ai.codes import normalize_code
from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.nl.models import Provenance, RouteSelection


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


def execution_plan(selection: RouteSelection) -> Provenance:
    endpoint = REST_ENDPOINTS.get(selection.operation)
    if endpoint is None:
        raise NaturalLanguageError("unknown_operation", "No REST mapping exists for this operation.", 422, operation=selection.operation)
    parameter = endpoint.parameter_name
    if set(selection.parameters) != {parameter}:
        raise NaturalLanguageError("malformed_parameters", "The selected operation has invalid parameter names.", 422, operation=selection.operation)
    try:
        code = normalize_code(selection.parameters[parameter], endpoint.code_length)
    except ValueError:
        raise NaturalLanguageError("malformed_parameters", "The selected country or currency code is invalid.", 422, operation=selection.operation) from None
    return Provenance(
        operation=selection.operation, parameters={parameter: code},
        rest_endpoint=endpoint.path_template.format(code=code),
    )
