from typing import Optional

import pydantic


class ProjectAssignment(pydantic.BaseModel):
    userId: str = pydantic.Field(...)
    roles: list[str] = pydantic.Field(...)
    # Returned by the Projects internal assignment route (stored for direct assignments, from the
    # identity provider for group-only users).
    userEmail: Optional[str] = pydantic.Field(None)
    userDisplayName: Optional[str] = pydantic.Field(None)
