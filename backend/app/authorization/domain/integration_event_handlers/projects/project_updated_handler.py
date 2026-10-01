from app.authorization.domain.integration_events.projects import project_updated
from app.authorization.domain.read_models import project_settings
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(event: project_updated.ProjectUpdated, uow: unit_of_work.UnitOfWork):
    """Keeps the authorizer's copy of the project's remote-support setting and of whether an external
    tool manages the project. Fields absent from the event (from before they existed) keep the stored
    value."""
    has_management = "managedBy" in event.model_fields_set
    if event.remoteSupportEnabled is None and not has_management:
        return

    with uow:
        settings_repo = uow.get_repository(project_settings.ProjectSettingsPrimaryKey, project_settings.ProjectSettings)
        settings_id = project_settings.ProjectSettingsPrimaryKey(projectId=event.projectId)

        stored = settings_repo.get(settings_id)
        settings = stored or project_settings.ProjectSettings(projectId=event.projectId)
        if event.remoteSupportEnabled is not None:
            settings.remoteSupportEnabled = event.remoteSupportEnabled
        if has_management:
            settings.managedBy = event.managedBy or None
            settings.managedSource = event.managedSource if event.managedBy else None
        if stored:
            settings_repo.update_entity(settings_id, settings)
        else:
            settings_repo.add(settings)
        uow.commit()
