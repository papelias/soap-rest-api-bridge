"""Application settings loaded from environment variables and an optional .env file."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from soap_rest_ai.policy.operations import APPROVED_OPERATIONS


class Settings(BaseSettings):
    """Configuration for the planned integrations; credentials are optional at startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: SecretStr | None = None
    anthropic_router_model: str = ""
    anthropic_answer_model: str = ""
    soap_wsdl_url: str = ""
    soap_endpoint_url: str = ""
    soap_allow_public_http_demo: bool = False
    soap_timeout_seconds: float = Field(default=10, gt=0)
    soap_max_retries: int = Field(default=1, ge=0)
    facade_base_url: str = "http://127.0.0.1:8000"
    wsdl_catalog_path: Path = Path("data/countryinfo_catalog.json")
    allowed_operations: tuple[str, ...] = APPROVED_OPERATIONS
    demo_mode: bool = False
    demo_daily_ask_limit: int = Field(default=60, gt=0)
