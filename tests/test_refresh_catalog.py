"""Catalog command behavior with isolated settings and loading."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from soap_rest_ai.config import Settings
from soap_rest_ai.soap import refresh_catalog
from soap_rest_ai.soap.catalog import serialize_catalog


def test_refresh_writes_catalog_using_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "data" / "catalog.json"
    settings = Settings(
        _env_file=None, soap_wsdl_url="https://example.test/service?WSDL",
        soap_timeout_seconds=3,
    )
    catalog = {
        "wsdl_url": settings.soap_wsdl_url,
        "operations": [{"name": "ExampleOperation"}],
    }
    loader = AsyncMock(return_value=catalog)
    monkeypatch.setattr(refresh_catalog, "Settings", lambda: settings)
    monkeypatch.setattr(refresh_catalog, "load_catalog", loader)
    monkeypatch.setattr("sys.argv", ["refresh_catalog", "--output", str(output)])

    refresh_catalog.main()

    loader.assert_awaited_once_with(settings.soap_wsdl_url, 3)
    assert output.read_bytes() == serialize_catalog(catalog).encode("utf-8")
    assert "Wrote 1 operations" in capsys.readouterr().out


def test_refresh_requires_wsdl_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None, soap_wsdl_url="")
    loader = AsyncMock()
    monkeypatch.setattr(refresh_catalog, "Settings", lambda: settings)
    monkeypatch.setattr(refresh_catalog, "load_catalog", loader)
    monkeypatch.setattr("sys.argv", ["refresh_catalog"])

    with pytest.raises(SystemExit) as error:
        refresh_catalog.main()

    assert error.value.code == 2
    assert "SOAP_WSDL_URL must be set" in capsys.readouterr().err
    loader.assert_not_awaited()


def test_failed_load_preserves_existing_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    output.write_text("existing catalog", encoding="utf-8")
    settings = Settings(_env_file=None, soap_wsdl_url="https://example.test/service?WSDL")
    monkeypatch.setattr(refresh_catalog, "Settings", lambda: settings)
    monkeypatch.setattr(
        refresh_catalog, "load_catalog", AsyncMock(side_effect=ValueError("Invalid WSDL"))
    )
    monkeypatch.setattr("sys.argv", ["refresh_catalog", "--output", str(output)])

    with pytest.raises(ValueError, match="Invalid WSDL"):
        refresh_catalog.main()

    assert output.read_text(encoding="utf-8") == "existing catalog"
