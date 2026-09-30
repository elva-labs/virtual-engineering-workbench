class DomainException(Exception):
    pass


class ReleasingProjectOnly(DomainException):
    """Only the deployment's releasing project releases base images."""


class BaseImageNotReleasedToRequiredChannel(DomainException):
    """A channel takes only an image that is or was in the channel it requires (prod requires test)."""
