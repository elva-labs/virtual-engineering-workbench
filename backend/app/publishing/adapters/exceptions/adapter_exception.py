class AdapterException(Exception):
    pass


class StoreImageTaskBusy(AdapterException):
    """Another store of the same image runs (EC2 allows one at a time per image); the state machine retries."""
