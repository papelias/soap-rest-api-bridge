"""Deterministic catalog extraction and serialization without public services."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from zeep.transports import Transport
from zeep.wsdl import Document

from soap_rest_ai.soap.catalog import extract_catalog, load_catalog, serialize_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.wsdl"
XSD = "{http://www.w3.org/2001/XMLSchema}"


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_remote_load(self: Transport, url: str) -> bytes:
        raise AssertionError(f"Unexpected network request: {url}")

    monkeypatch.setattr(Transport, "_load_remote_data", reject_remote_load)


@pytest.fixture
def wsdl() -> Iterator[Document]:
    transport = Transport()
    try:
        yield Document(str(FIXTURE), transport=transport)
    finally:
        transport.session.close()


def test_extract_operations_types_and_faults(wsdl: Document) -> None:
    catalog = extract_catalog(wsdl)
    operations = catalog["operations"]
    assert [op["name"] for op in operations] == [
        "CapitalCity", "CapitalCity", "FullCountryInfo", "MultipleParts", "Ping"
    ]
    assert operations[0] == {
        "name": "CapitalCity",
        "port_type": "{urn:catalog-test}CountryPort",
        "input_parameters": [{"name": "sCountryISOCode", "type": f"{XSD}string"}],
        "output_type": f"{XSD}string",
        "faults": [],
    }
    assert operations[1]["port_type"] == "{urn:catalog-test}OtherPort"
    assert operations[1]["output_type"] is None
    country = operations[2]
    assert country["output_type"] == "{urn:catalog-test}CountryInfo"
    assert country["faults"] == [
        {
            "name": name,
            "message": "{urn:catalog-test}ErrorMessage",
            "parameters": [{"name": "detail", "type": "{urn:catalog-test}ErrorDetail"}],
        }
        for name in ["InvalidCode", "Unavailable"]
    ]
    assert operations[3]["input_parameters"] == [
        {"name": "zCount", "type": f"{XSD}int"},
        {"name": "aName", "type": f"{XSD}string"},
    ]
    assert operations[3]["output_type"] == f"(zCount: {XSD}int, aName: {XSD}string)"
    assert operations[4]["input_parameters"] == []
    assert operations[4]["output_type"] is None


def test_extraction_order_is_stable(wsdl: Document) -> None:
    expected = serialize_catalog(extract_catalog(wsdl))
    wsdl.port_types = dict(reversed(list(wsdl.port_types.items())))
    for port_type in wsdl.port_types.values():
        port_type.operations = dict(reversed(list(port_type.operations.items())))
        for operation in port_type.operations.values():
            operation.fault_messages = dict(reversed(list(operation.fault_messages.items())))
    assert serialize_catalog(extract_catalog(wsdl)) == expected


def test_serialization_is_readable_and_deterministic() -> None:
    catalog = {"wsdl_url": "https://example.test/service?WSDL", "operations": []}
    expected = (
        '{\n  "operations": [],\n'
        '  "wsdl_url": "https://example.test/service?WSDL"\n}\n'
    )
    assert serialize_catalog(catalog) == expected
    assert serialize_catalog(dict(reversed(list(catalog.items())))) == expected
    assert json.loads(expected) == catalog


@pytest.mark.asyncio
async def test_load_catalog_from_local_wsdl(wsdl: Document) -> None:
    assert await load_catalog(str(FIXTURE), timeout=1) == extract_catalog(wsdl)
