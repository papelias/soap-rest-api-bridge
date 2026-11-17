"""Manual end-to-end checks against a running application; no mocked fallback."""

import asyncio
import json

import httpx
from pydantic import ValidationError

from soap_rest_ai.config import Settings

CASES = [
    ("What is the capital of Japan?", "CapitalCity", {"sCountryISOCode": "JP"}, "/countries/JP/capital"),
    ("What currency does the Philippines use?", "CountryCurrency", {"sCountryISOCode": "PH"}, "/countries/PH/currency"),
    ("What is Germany's international phone code?", "CountryIntPhoneCode", {"sCountryISOCode": "DE"}, "/countries/DE/phone-code"),
    ("Give me full information about Canada.", "FullCountryInfo", {"sCountryISOCode": "CA"}, "/countries/CA"),
    ("Which countries use EUR?", "CountriesUsingCurrency", {"sISOCurrencyCode": "EUR"}, "/currencies/EUR/countries"),
]


async def validate() -> int:
    try:
        settings = Settings()
    except ValidationError:
        print(json.dumps({"status": "blocked", "reason": "Invalid application configuration."}))
        return 2
    required = {
        "ANTHROPIC_API_KEY": bool(settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value().strip()),
        "ANTHROPIC_ROUTER_MODEL": bool(settings.anthropic_router_model.strip()),
        "ANTHROPIC_ANSWER_MODEL": bool(settings.anthropic_answer_model.strip()),
        "SOAP_WSDL_URL": bool(settings.soap_wsdl_url.strip()),
    }
    missing = [name for name, configured in required.items() if not configured]
    if missing:
        print(json.dumps({"status": "blocked", "missing_settings": missing}))
        return 2

    passed = 0
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        for question, operation, parameters, endpoint in CASES:
            try:
                response = await client.post(
                    settings.facade_base_url.rstrip("/") + "/ask", json={"question": question}
                )
                body = response.json()
                ok = (
                    response.status_code == 200 and isinstance(body, dict)
                    and isinstance(body.get("answer"), str) and bool(body["answer"].strip())
                    and body.get("provenance") == {
                        "operation": operation, "parameters": parameters, "rest_endpoint": endpoint
                    }
                )
                print(json.dumps({"question": question, "status": response.status_code, "ok": ok, "response": body}, ensure_ascii=False))
            except (httpx.HTTPError, ValueError):
                ok = False
                print(json.dumps({"question": question, "ok": False, "error": "Could not obtain a valid response from the running application."}))
            passed += int(ok)
    print(json.dumps({"passed": passed, "total": len(CASES)}))
    return 0 if passed == len(CASES) else 1


def main() -> None:
    raise SystemExit(asyncio.run(validate()))


if __name__ == "__main__":
    main()
