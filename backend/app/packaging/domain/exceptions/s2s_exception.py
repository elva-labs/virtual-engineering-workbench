class S2SException(Exception):
    def __init__(self, detail: str, *, code: str, retryable: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.retryable = retryable


class InvalidIdempotencyKey(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key must be a valid UUID.",
            code="INVALID_IDEMPOTENCY_KEY",
            retryable=False,
        )


class IdempotencyKeyReused(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "The idempotency key was already used with a different request.",
            code="IDEMPOTENCY_KEY_REUSED",
            retryable=False,
        )


class IdempotencyRequestInProgress(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "A request with this idempotency key is still in progress.",
            code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
            retryable=True,
        )


class ResourceReadNotReady(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "The created resource is not ready to be read.",
            code="RESOURCE_READ_NOT_READY",
            retryable=True,
        )


class StoredDefinitionInvalid(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "The stored component definition is invalid.",
            code="STORED_DEFINITION_INVALID",
            retryable=False,
        )


class ReplayedCreateFailure(S2SException):
    def __init__(self, *, status_code: int, detail: str, code: str, retryable: bool) -> None:
        super().__init__(detail, code=code, retryable=retryable)
        self.status_code = status_code


class ProjectAccessDenied(S2SException):
    def __init__(self) -> None:
        super().__init__(
            "The service client is not assigned to this project.", code="PROJECT_ACCESS_DENIED", retryable=False
        )


class ProjectAccessUnavailable(S2SException):
    def __init__(self) -> None:
        super().__init__("Project access could not be verified.", code="PROJECT_ACCESS_UNAVAILABLE", retryable=True)


class InsufficientScope(S2SException):
    def __init__(self, required_scope: str) -> None:
        super().__init__(
            f"The access token does not include the required scope: {required_scope}.",
            code="INSUFFICIENT_SCOPE",
            retryable=False,
        )
