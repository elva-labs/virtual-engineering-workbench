from app.shared.adapters.unit_of_work_v2 import unit_of_work


class ProjectCreateRequestPrimaryKey(unit_of_work.PrimaryKey):
    clientId: str
    idempotencyKey: str


class ProjectCreateRequest(unit_of_work.Entity):
    clientId: str
    idempotencyKey: str
    requestHash: str
    projectId: str
