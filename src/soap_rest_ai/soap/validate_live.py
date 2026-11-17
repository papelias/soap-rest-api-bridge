"""Explicit manual live command, separate from application startup and pytest."""

import asyncio
import json

from soap_rest_ai.config import Settings
from soap_rest_ai.soap.client import CountryInfoAdapter, JsonValue
from soap_rest_ai.soap.errors import SoapIntegrationError


def _matches_sample(operation: str, result: JsonValue) -> bool:
    if operation == "CapitalCity":
        return result == "Manila"
    if operation == "CountryIntPhoneCode":
        return result == "63"
    if operation == "CountryCurrency":
        return isinstance(result, dict) and result.get("sISOCode") == "PHP"
    if operation == "FullCountryInfo":
        return isinstance(result, dict) and result.get("sISOCode") == "PH"
    return isinstance(result, list) and any(
        isinstance(country, dict) and country.get("sISOCode") == "PH"
        for country in result
    )


async def validate() -> bool:
    """Call all five operations and verify representative Philippine data."""
    passed = True
    async with CountryInfoAdapter(Settings()) as adapter:
        cases = [
            ("CapitalCity", adapter.capital_city, "PH"),
            ("CountryCurrency", adapter.country_currency, "PH"),
            ("CountryIntPhoneCode", adapter.country_int_phone_code, "PH"),
            ("FullCountryInfo", adapter.full_country_info, "PH"),
            ("CountriesUsingCurrency", adapter.countries_using_currency, "PHP"),
        ]
        for operation, call, code in cases:
            try:
                result = await call(code)
                ok = _matches_sample(operation, result)
                report = {"operation": operation, "ok": ok, "result": result}
            except SoapIntegrationError as exc:
                ok = False
                report = {"operation": operation, "ok": False, "error": exc.to_dict()}
            passed = passed and ok
            print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return passed


def main() -> None:
    raise SystemExit(0 if asyncio.run(validate()) else 1)


if __name__ == "__main__":
    main()
