"""Sanitized failures returned by the Publishing service-to-service API."""


class S2SException(Exception):
    code = "INTERNAL_ERROR"
    detail = "Internal server error."
    retryable = True


class InvalidIdempotencyKey(S2SException):
    code = "INVALID_IDEMPOTENCY_KEY"
    detail = "A valid UUID Idempotency-Key header is required."
    retryable = False


class IdempotencyKeyReused(S2SException):
    code = "IDEMPOTENCY_KEY_REUSED"
    detail = "The Idempotency-Key was already used for a different request."
    retryable = False


class IdempotencyRequestInProgress(S2SException):
    code = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    detail = "A request with this Idempotency-Key is still in progress."
    retryable = True


class InsufficientScope(S2SException):
    code = "INSUFFICIENT_SCOPE"
    detail = "The access token does not grant the required scope."
    retryable = False


class ProjectAccessDenied(S2SException):
    code = "PROJECT_ACCESS_DENIED"
    detail = "The client is not assigned to this project."
    retryable = False


class ProjectAccessUnavailable(S2SException):
    code = "PROJECT_ACCESS_UNAVAILABLE"
    detail = "Project access could not be verified."
    retryable = True


class ResourceReadNotReady(S2SException):
    code = "RESOURCE_READ_NOT_READY"
    detail = "The resource state could not be verified. Retry the request."
    retryable = True


class ResourceNotFound(S2SException):
    code = "NOT_FOUND"
    detail = "The requested resource was not found in this project."
    retryable = False


class TechnologyNotFound(S2SException):
    code = "TECHNOLOGY_NOT_FOUND"
    detail = "The technology does not exist in this project."
    retryable = False


class ReleasingProjectOnly(S2SException):
    code = "RELEASING_PROJECT_ONLY"
    detail = "Only the releasing program creates platform products."
    retryable = False


class InvalidRequest(S2SException):
    code = "INVALID_REQUEST"
    detail = "The request does not match the API contract."
    retryable = False


class Unauthorized(S2SException):
    code = "UNAUTHORIZED"
    detail = "Authentication is required."
    retryable = False


class ResourceConflict(S2SException):
    code = "RESOURCE_CONFLICT"
    detail = "The request conflicts with the current resource state."
    retryable = False


class ReplayedCreateFailure(S2SException):
    def __init__(self, status_code: int, detail: str, code: str, retryable: bool):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.retryable = retryable
        super().__init__(detail)
