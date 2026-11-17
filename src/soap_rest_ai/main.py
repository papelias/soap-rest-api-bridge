"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from soap_rest_ai.api.ask import router as ask_router
from soap_rest_ai.api.demo import DemoAskLimits, DemoSecurityMiddleware
from soap_rest_ai.api.errors import register_error_handlers
from soap_rest_ai.api.facade import router as facade_router
from soap_rest_ai.api.health import router as health_router
from soap_rest_ai.config import Settings
from soap_rest_ai.nl.service import NaturalLanguageService
from soap_rest_ai.soap.client import CountryInfoAdapter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    async with CountryInfoAdapter(settings) as adapter, NaturalLanguageService(settings) as nl_service:
        app.state.settings = settings
        app.state.soap_adapter = adapter
        app.state.nl_service = nl_service
        app.state.demo_limits = DemoAskLimits(settings.demo_daily_ask_limit) if settings.demo_mode else None
        try:
            yield
        finally:
            app.state.demo_limits = None


app = FastAPI(title="SOAP-to-REST AI Integration Layer", lifespan=lifespan)
app.add_middleware(DemoSecurityMiddleware)
register_error_handlers(app)
app.include_router(health_router)
app.include_router(facade_router)
app.include_router(ask_router)
