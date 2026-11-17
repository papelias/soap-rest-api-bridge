"""Provider-independent natural-language request, routing, and response models."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: str = Field(min_length=1)
    parameters: dict[str, str]


class Provenance(BaseModel):
    operation: str
    parameters: dict[str, str]
    rest_endpoint: str


class AskResponse(BaseModel):
    answer: str = Field(min_length=1)
    provenance: Provenance


@dataclass
class RoutingTrace:
    """Optional evaluation observations; never used to make execution decisions."""

    selection: RouteSelection | None = None
    plan: Provenance | None = None
    routing_ms: float | None = None
