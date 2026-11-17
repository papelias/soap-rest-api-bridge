"""REST response schemas and normalized path parameter types."""

from functools import partial
from typing import Annotated, Literal

from fastapi import Path
from pydantic import AliasPath, BaseModel, BeforeValidator, Field

from soap_rest_ai.codes import normalize_code

CountryCode = Annotated[
    str,
    BeforeValidator(partial(normalize_code, length=2)),
    Path(pattern="^[A-Z]{2}$", description="Two-letter country code; normalized to uppercase.", examples=["PH"]),
]
CurrencyCode = Annotated[
    str,
    BeforeValidator(partial(normalize_code, length=3)),
    Path(pattern="^[A-Z]{3}$", description="Three-letter currency code; normalized to uppercase.", examples=["PHP"]),
]


class Currency(BaseModel):
    iso_code: str = Field(validation_alias="sISOCode")
    name: str = Field(validation_alias="sName")


class CountrySummary(BaseModel):
    iso_code: str = Field(validation_alias="sISOCode")
    name: str = Field(validation_alias="sName")


class Language(BaseModel):
    iso_code: str = Field(validation_alias="sISOCode")
    name: str = Field(validation_alias="sName")


class CountryInfo(BaseModel):
    iso_code: str = Field(validation_alias="sISOCode")
    name: str = Field(validation_alias="sName")
    capital: str = Field(validation_alias="sCapitalCity")
    phone_code: str = Field(validation_alias="sPhoneCode")
    continent_code: str = Field(validation_alias="sContinentCode")
    currency_code: str = Field(validation_alias="sCurrencyISOCode")
    flag_url: str = Field(validation_alias="sCountryFlag")
    languages: list[Language | None] | None = Field(
        default=None, validation_alias=AliasPath("Languages", "tLanguage")
    )


class CapitalResponse(BaseModel):
    operation: Literal["CapitalCity"] = "CapitalCity"
    iso_code: str
    capital: str | None


class CurrencyResponse(BaseModel):
    operation: Literal["CountryCurrency"] = "CountryCurrency"
    iso_code: str
    currency: Currency | None


class PhoneCodeResponse(BaseModel):
    operation: Literal["CountryIntPhoneCode"] = "CountryIntPhoneCode"
    iso_code: str
    phone_code: str | None


class CountryInfoResponse(BaseModel):
    operation: Literal["FullCountryInfo"] = "FullCountryInfo"
    iso_code: str
    country: CountryInfo | None


class CurrencyCountriesResponse(BaseModel):
    operation: Literal["CountriesUsingCurrency"] = "CountriesUsingCurrency"
    currency_code: str
    countries: list[CountrySummary | None] | None


class RestError(BaseModel):
    code: str
    message: str
    operation: str | None = None
    retryable: bool = False
    fault_code: str | None = None
    upstream_status: int | None = None


class ErrorResponse(BaseModel):
    error: RestError
