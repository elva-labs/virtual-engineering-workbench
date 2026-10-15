from pydantic import BaseModel


class ReconcileStuckVersions(BaseModel):
    """The hourly schedule's event: retire versions stuck in UPDATING that Image Builder never created."""
