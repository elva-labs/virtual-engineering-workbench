from pydantic import BaseModel


class SpokeCapacityJob(BaseModel):
    """Read every enrolled spoke's service quotas and what uses them (capacity.everyMinutes),
    store the snapshot for the launch check and the admin dashboard, and refresh open quota requests."""
