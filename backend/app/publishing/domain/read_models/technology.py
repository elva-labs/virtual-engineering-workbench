from pydantic import BaseModel, Field


class Technology(BaseModel):
    """A project technology as the Projects bounded context reports it."""

    technologyId: str = Field(..., title="TechnologyId")
    technologyName: str = Field(..., title="TechnologyName")
