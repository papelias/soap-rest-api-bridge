"""Extract and serialize operation metadata from a Zeep WSDL document."""

import asyncio
import json
from typing import Any

import httpx
from zeep import xsd
from zeep.wsdl import Document
from zeep.wsdl.definitions import AbstractMessage

from soap_rest_ai.soap.wsdl import load_wsdl


def _type_name(schema_type: xsd.Type) -> str:
    """Use namespace URIs instead of document-specific namespace prefixes."""
    if schema_type.qname is not None:
        return str(schema_type.qname)
    return schema_type.signature()


def _parameters(
    message: AbstractMessage | None, wrapper_name: str | None = None
) -> list[dict[str, str]]:
    if message is None:
        return []

    parts = list(message.parts.items())
    if len(parts) == 1:
        element = parts[0][1].element
        if (
            element is not None
            and element.name == wrapper_name
            and isinstance(element.type, xsd.ComplexType)
        ):
            return [
                {"name": name, "type": _type_name(child.type)}
                for name, child in element.type.elements
            ]

    return [
        {
            "name": name,
            "type": _type_name(
                part.element.type if part.element is not None else part.type
            ),
        }
        for name, part in parts
    ]


def _output_type(parameters: list[dict[str, str]]) -> str | None:
    if not parameters:
        return None
    if len(parameters) == 1:
        return parameters[0]["type"]
    return "(" + ", ".join(f"{p['name']}: {p['type']}" for p in parameters) + ")"


def extract_catalog(wsdl: Document) -> dict[str, Any]:
    """Describe abstract operations once, independent of SOAP binding versions."""
    port_types = dict(wsdl.port_types)
    for binding in wsdl.bindings.values():
        port_types[str(binding.port_type.name)] = binding.port_type

    operations = []
    for port_type in port_types.values():
        for operation in port_type.operations.values():
            output_parameters = _parameters(
                operation.output_message, f"{operation.name}Response"
            )
            operations.append(
                {
                    "name": operation.name,
                    "port_type": str(port_type.name),
                    "input_parameters": _parameters(
                        operation.input_message, operation.name
                    ),
                    "output_type": _output_type(output_parameters),
                    "faults": [
                        {
                            "name": name,
                            "message": str(message.name),
                            "parameters": _parameters(message),
                        }
                        for name, message in sorted(operation.fault_messages.items())
                    ],
                }
            )

    return {
        "wsdl_url": wsdl.location,
        "operations": sorted(operations, key=lambda op: (op["name"], op["port_type"])),
    }


def serialize_catalog(catalog: dict[str, Any]) -> str:
    """Emit readable, stable JSON without timestamps or platform line endings."""
    return json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


async def load_catalog(wsdl_url: str, timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as http:
        document = await load_wsdl(wsdl_url, http)
    return await asyncio.to_thread(extract_catalog, document)
