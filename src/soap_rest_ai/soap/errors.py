"""Provider-independent errors exposed by the SOAP adapter."""

from enum import StrEnum


class SoapErrorKind(StrEnum):
    VALIDATION_SERIALIZATION = "validation_serialization"
    SOAP_FAULT = "soap_fault"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


class SoapIntegrationError(Exception):
    """A SOAP boundary failure containing only JSON-serializable metadata."""

    def __init__(
        self,
        kind: SoapErrorKind,
        operation: str,
        message: str,
        *,
        retryable: bool = False,
        fault_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.operation = operation
        self.message = message
        self.retryable = retryable
        self.fault_code = fault_code
        self.http_status = http_status

    def to_dict(self) -> dict[str, str | bool | int | None]:
        return {
            "kind": self.kind.value,
            "operation": self.operation,
            "message": self.message,
            "retryable": self.retryable,
            "fault_code": self.fault_code,
            "http_status": self.http_status,
        }
