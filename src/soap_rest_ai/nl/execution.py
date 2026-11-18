"""Explicit operation-to-REST mapping with deterministic parameter validation."""

from soap_rest_ai.codes import normalize_code
from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.nl.models import Provenance, RouteSelection
from soap_rest_ai.rest_endpoints import REST_ENDPOINTS


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
