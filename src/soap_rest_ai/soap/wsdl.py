"""Load a self-contained WSDL from an operator-configured source."""

import asyncio
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from zeep import Settings
from zeep.transports import Transport
from zeep.wsdl import Document


def https_url(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if url.scheme != "https" or not url.host or url.userinfo or url.fragment:
        raise ValueError("SOAP URLs must use HTTPS without credentials or fragments.")
    return url


def wsdl_url(source: str) -> httpx.URL | None:
    if Path(source).drive or not urlsplit(source).scheme:
        return None
    return https_url(source)


def xml_settings() -> Settings:
    return Settings(forbid_dtd=True, forbid_entities=True, forbid_external=True)


class _SelfContainedWsdl(Transport):
    def load(self, url: str) -> bytes:
        # The entry point is passed as bytes. Any further load is an import.
        raise ValueError("External WSDL and XSD imports are disabled.")


async def load_wsdl(source: str, http: httpx.AsyncClient) -> Document:
    remote = wsdl_url(source)
    if remote is None:
        path = Path(source)
        if source.startswith("//") or path.drive.startswith("\\\\"):
            raise ValueError("WSDL paths must refer to local files.")
        path = path.absolute()
        source = str(path)
        content = await asyncio.to_thread(path.read_bytes)
    else:
        response = await http.get(remote, follow_redirects=False)
        response.raise_for_status()
        content = response.content

    return await asyncio.to_thread(_parse_wsdl, content, source)


def _parse_wsdl(content: bytes, source: str) -> Document:
    transport = _SelfContainedWsdl()
    try:
        return Document(BytesIO(content), transport, base=source, settings=xml_settings())
    finally:
        transport.session.close()
