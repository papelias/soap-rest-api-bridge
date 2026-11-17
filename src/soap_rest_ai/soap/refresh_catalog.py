"""Explicit CLI for refreshing the local WSDL catalog."""

import argparse
import asyncio
from pathlib import Path

from soap_rest_ai.config import Settings
from soap_rest_ai.soap.catalog import load_catalog, serialize_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the local WSDL catalog.")
    parser.add_argument(
        "--output", type=Path, default=Path("data/countryinfo_catalog.json")
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.soap_wsdl_url.strip():
        parser.error("SOAP_WSDL_URL must be set in the environment or .env")

    catalog = asyncio.run(
        load_catalog(settings.soap_wsdl_url, settings.soap_timeout_seconds)
    )
    content = serialize_catalog(catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {len(catalog['operations'])} operations to {args.output}")
    for operation in catalog["operations"]:
        print(f"  {operation['name']}")


if __name__ == "__main__":
    main()
