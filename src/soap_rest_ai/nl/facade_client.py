"""Fetch and validate results from the REST facade."""

import json
from typing import Any

import httpx

from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.nl.models import Provenance
from soap_rest_ai.rest_endpoints import REST_ENDPOINTS


async def fetch_facade_result(
    client: httpx.AsyncClient, base_url: str, plan: Provenance
) -> dict[str, Any]:
    try:
        response = await client.get(base_url.rstrip("/") + plan.rest_endpoint)
    except httpx.TimeoutException:
        raise NaturalLanguageError(
            "facade_timeout", "The REST facade timed out.", 504,
            operation=plan.operation, retryable=True,
        ) from None
    except (httpx.RequestError, httpx.InvalidURL):
        raise NaturalLanguageError(
            "facade_unavailable", "The REST facade could not be reached.", 503,
            operation=plan.operation, retryable=True,
        ) from None

    upstream_status = response.status_code
    if upstream_status != 200:
        status = upstream_status if upstream_status in {403, 422, 502, 503, 504} else 502
        raise NaturalLanguageError(
            "facade_error", f"The REST facade returned HTTP {upstream_status}.", status,
            operation=plan.operation, retryable=upstream_status >= 500 or upstream_status == 429,
            upstream_status=upstream_status,
        )

    try:
        result = response.json()
        if not isinstance(result, dict) or result.get("operation") != plan.operation:
            raise ValueError("Unexpected facade result")
        endpoint = REST_ENDPOINTS[plan.operation]
        expected_code = plan.parameters[endpoint.parameter_name]
        if result.get(endpoint.response_code_field) != expected_code or endpoint.result_field not in result:
            raise ValueError("Mismatched facade result")
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError):
        raise NaturalLanguageError(
            "invalid_facade_response", "The REST facade returned an invalid result.", 502,
            operation=plan.operation,
        ) from None
    return result
