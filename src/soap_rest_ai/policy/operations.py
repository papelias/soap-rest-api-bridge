"""Application execution policy, independent of WSDL discovery."""

from collections.abc import Collection

APPROVED_OPERATIONS = (
    "CapitalCity",
    "CountryCurrency",
    "CountryIntPhoneCode",
    "FullCountryInfo",
    "CountriesUsingCurrency",
)


class OperationBlockedError(Exception):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Operation {operation} is not permitted.")


def require_operation(operation: str, allowed_operations: Collection[str]) -> None:
    """Configuration may restrict the approved MVP operations, never expand them."""
    if operation not in APPROVED_OPERATIONS or operation not in allowed_operations:
        raise OperationBlockedError(operation)
