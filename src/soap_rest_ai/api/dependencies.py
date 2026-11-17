"""Access application-owned settings and the replaceable SOAP adapter."""

from fastapi import Request

from soap_rest_ai.config import Settings
from soap_rest_ai.nl.service import NaturalLanguageService
from soap_rest_ai.soap.client import CountryInfoAdapter


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_adapter(request: Request) -> CountryInfoAdapter:
    return request.app.state.soap_adapter


def get_nl_service(request: Request) -> NaturalLanguageService:
    return request.app.state.nl_service
