"""Execution permission is independent of WSDL operation discovery."""

import json
from pathlib import Path

import pytest

from soap_rest_ai.config import Settings
from soap_rest_ai.policy.operations import (
    APPROVED_OPERATIONS, OperationBlockedError, require_operation,
)


def test_default_allowlist_is_exactly_the_five_approved_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOWED_OPERATIONS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.allowed_operations == APPROVED_OPERATIONS
    for operation in APPROVED_OPERATIONS:
        require_operation(operation, settings.allowed_operations)


def test_configuration_can_restrict_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_OPERATIONS", '["CountryCurrency"]')
    settings = Settings(_env_file=None)
    assert settings.allowed_operations == ("CountryCurrency",)
    require_operation("CountryCurrency", settings.allowed_operations)
    with pytest.raises(OperationBlockedError):
        require_operation("CapitalCity", settings.allowed_operations)


def test_catalog_discovery_cannot_grant_execution_permission() -> None:
    path = Path(__file__).parents[1] / "data" / "countryinfo_catalog.json"
    discovered = [operation["name"] for operation in json.loads(path.read_text())["operations"]]
    assert "CountryFlag" in discovered
    with pytest.raises(OperationBlockedError) as error:
        require_operation("CountryFlag", discovered)
    assert error.value.operation == "CountryFlag"


def test_empty_allowlist_blocks_approved_operations() -> None:
    with pytest.raises(OperationBlockedError):
        require_operation("CapitalCity", ())
