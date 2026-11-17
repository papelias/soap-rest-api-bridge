"""One REST error envelope for validation, policy, and upstream failures."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from soap_rest_ai.api.models import ErrorResponse, RestError
from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.policy.operations import OperationBlockedError
from soap_rest_ai.soap.errors import SoapErrorKind, SoapIntegrationError


def _operation(request: Request) -> str | None:
    return getattr(request.scope.get("route"), "operation_id", None)


def _response(status: int, error: RestError) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=error).model_dump())


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _response(422, RestError(
        code="invalid_input", message=exc.errors()[0]["msg"], operation=_operation(request)
    ))


async def blocked_operation(request: Request, exc: OperationBlockedError) -> JSONResponse:
    return _response(403, RestError(
        code="operation_blocked", message=str(exc), operation=exc.operation
    ))


async def soap_error(request: Request, exc: SoapIntegrationError) -> JSONResponse:
    statuses = {
        SoapErrorKind.SOAP_FAULT: 502,
        SoapErrorKind.UPSTREAM_TIMEOUT: 504,
        SoapErrorKind.UPSTREAM_UNAVAILABLE: 503,
        SoapErrorKind.VALIDATION_SERIALIZATION: 502,
    }
    return _response(statuses[exc.kind], RestError(
        code=exc.kind.value, message=exc.message, operation=exc.operation,
        retryable=exc.retryable, fault_code=exc.fault_code, upstream_status=exc.http_status,
    ))


async def invalid_response(request: Request, exc: ResponseValidationError) -> JSONResponse:
    return _response(502, RestError(
        code="invalid_upstream_response", message="SOAP result did not match the REST response schema.",
        operation=_operation(request),
    ))


async def nl_error(request: Request, exc: NaturalLanguageError) -> JSONResponse:
    return _response(exc.status_code, RestError(
        code=exc.code, message=exc.message, operation=exc.operation,
        retryable=exc.retryable, upstream_status=exc.upstream_status,
    ))


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    response = _response(exc.status_code, RestError(
        code="http_error", message=str(exc.detail), operation=_operation(request)
    ))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def internal_error(request: Request, exc: Exception) -> JSONResponse:
    return _response(500, RestError(
        code="internal_error", message="An unexpected server error occurred.",
        operation=_operation(request),
    ))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(OperationBlockedError, blocked_operation)
    app.add_exception_handler(SoapIntegrationError, soap_error)
    app.add_exception_handler(NaturalLanguageError, nl_error)
    app.add_exception_handler(ResponseValidationError, invalid_response)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, internal_error)
