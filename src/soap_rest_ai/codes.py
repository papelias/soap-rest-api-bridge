"""Shared country/currency code normalization for REST and SOAP boundaries."""

import re


def normalize_code(value: str, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"[A-Za-z]{{{length}}}", value.strip()
    ):
        raise ValueError(f"Code must contain {length} ASCII letters.")
    return value.strip().upper()
