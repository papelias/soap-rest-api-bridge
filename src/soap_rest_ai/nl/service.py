"""Orchestrate two model calls around a guarded HTTP call to the REST facade."""

import asyncio
import json
from types import TracebackType
from time import perf_counter
from typing import Any, Self

import httpx
from anthropic import AsyncAnthropic

from soap_rest_ai.config import Settings
from soap_rest_ai.nl.answer import generate_answer
from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.nl.execution import execution_plan
from soap_rest_ai.nl.facade_client import fetch_facade_result
from soap_rest_ai.nl.models import AskResponse, RoutingTrace
from soap_rest_ai.nl.router import route_question
from soap_rest_ai.policy.operations import require_operation


class NaturalLanguageService:
    def __init__(self, settings: Settings, *, facade_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = facade_client or httpx.AsyncClient(
            timeout=30.0, follow_redirects=False, trust_env=not settings.demo_mode,
        )
        self._owns_http = facade_client is None
        self._llm: AsyncAnthropic | None = None
        self._catalog: dict[str, Any] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._llm is not None:
            await self._llm.close()
        if self._owns_http:
            await self._http.aclose()

    async def _get_catalog(self) -> dict[str, Any]:
        if self._catalog is None:
            try:
                text = await asyncio.to_thread(self._settings.wsdl_catalog_path.read_text, encoding="utf-8")
                catalog = json.loads(text)
                if not isinstance(catalog, dict) or not isinstance(catalog.get("operations"), list) or not catalog["operations"]:
                    raise ValueError("Invalid catalog")
                if any(not isinstance(op, dict) or not isinstance(op.get("name"), str) or not op["name"] for op in catalog["operations"]):
                    raise ValueError("Invalid operation")
                self._catalog = catalog
            except (OSError, ValueError):
                raise NaturalLanguageError("catalog_unavailable", "The local WSDL catalog could not be loaded.", 503) from None
        return self._catalog

    async def ask(self, question: str, *, trace: RoutingTrace | None = None) -> AskResponse:
        settings = self._settings
        if not (
            settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value().strip()
            and settings.anthropic_router_model.strip() and settings.anthropic_answer_model.strip()
        ):
            raise NaturalLanguageError("nl_not_configured", "Natural-language model settings are not configured.", 503)
        catalog = await self._get_catalog()
        if self._llm is None:
            self._llm = AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(), timeout=30.0, max_retries=0
            )
        started = perf_counter()
        try:
            selection = await route_question(self._llm, settings.anthropic_router_model, question, catalog)
        finally:
            if trace is not None:
                trace.routing_ms = (perf_counter() - started) * 1000
        if trace is not None:
            trace.selection = selection.model_copy(deep=True)
        if selection.operation not in {op["name"] for op in catalog["operations"]}:
            raise NaturalLanguageError("unknown_operation", "The selected operation is not in the WSDL catalog.", 422, operation=selection.operation)
        require_operation(selection.operation, settings.allowed_operations)
        plan = execution_plan(selection)
        if trace is not None:
            trace.plan = plan.model_copy(deep=True)

        result = await fetch_facade_result(self._http, settings.facade_base_url, plan)

        try:
            answer = await generate_answer(self._llm, settings.anthropic_answer_model, question, result)
        except NaturalLanguageError as exc:
            exc.operation = plan.operation
            raise
        return AskResponse(answer=answer, provenance=plan)
