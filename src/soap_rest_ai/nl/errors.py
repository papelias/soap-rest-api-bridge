"""Safe, provider-independent failures exposed by the natural-language layer."""


class NaturalLanguageError(Exception):
    def __init__(
        self, code: str, message: str, status_code: int,
        *, operation: str | None = None, retryable: bool = False,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.operation = operation
        self.retryable = retryable
        self.upstream_status = upstream_status
