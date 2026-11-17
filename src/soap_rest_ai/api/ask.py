"""Natural-language HTTP API boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends

from soap_rest_ai.api.dependencies import get_nl_service
from soap_rest_ai.api.models import ErrorResponse
from soap_rest_ai.nl.models import AskRequest, AskResponse
from soap_rest_ai.nl.service import NaturalLanguageService

router = APIRouter(tags=["Natural language"])


@router.post(
    "/ask", response_model=AskResponse, operation_id="Ask",
    summary="Ask one country-information question",
    description="Route using Anthropic tools, enforce execution policy, call the REST facade over HTTP, and generate an answer with application-owned provenance.",
    responses={status: {"model": ErrorResponse} for status in (403, 413, 422, 429, 500, 502, 503, 504)},
)
async def ask(
    body: AskRequest, service: Annotated[NaturalLanguageService, Depends(get_nl_service)]
) -> AskResponse:
    return await service.ask(body.question)
