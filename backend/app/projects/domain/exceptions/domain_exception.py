class DomainException(Exception):
    pass


class TechnologyInUseException(DomainException):
    """A technology cannot be removed while any project account references it."""


class ProjectAccountStateConflict(DomainException):
    """A project account cannot be deactivated from its current lifecycle state."""
