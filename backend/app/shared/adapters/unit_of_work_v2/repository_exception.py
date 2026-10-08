class RepositoryException(Exception):
    pass


class ConditionalCheckFailedException(RepositoryException):
    """A write's condition did not hold (e.g. the entity changed since it was read)."""

    pass


class TransactionConflictException(RepositoryException):
    """Another transaction was writing the same item; re-read and retry (DynamoDB TransactionConflict)."""

    pass
