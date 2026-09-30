import logging
from datetime import datetime, timezone
from uuid import uuid4

import assertpy

from app.projects.domain.commands.project_accounts import (
    deactivate_project_account_s2s_command,
    on_board_project_account_command,
    update_project_account_command,
)
from app.projects.domain.commands.technologies import (
    add_technology,
    delete_technology_command,
    update_technology_command,
)
from app.projects.entrypoints.s2s_api import bootstrapper, config
from app.shared.domain.ports.idempotency_service import IdempotencyScope, ReservationOutcome


def test_bootstrapper():
    # ARRANGE
    app_config = config.AppConfig(cors_config=config.config.get("cors_config"))
    # ACT
    dependencies = bootstrapper.bootstrap(app_config=app_config, logger=logging.getLogger())

    # ASSERT
    assertpy.assert_that(dependencies).is_not_none()
    registered_handlers = dependencies.command_bus._inner._command_handlers
    assert add_technology.AddTechnologyCommand.__name__ in registered_handlers
    assert update_technology_command.UpdateTechnologyCommand.__name__ in registered_handlers
    assert delete_technology_command.DeleteTechnologyCommand.__name__ in registered_handlers
    assert on_board_project_account_command.OnBoardProjectAccountCommand.__name__ in registered_handlers
    assert update_project_account_command.UpdateProjectAccountCommand.__name__ in registered_handlers
    assert deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand.__name__ in registered_handlers
    assert callable(dependencies.technologies_query_service.get_technology_by_id)
    reservation = dependencies.idempotency_service.reserve(
        IdempotencyScope("client", "project", "CREATE", None, uuid4()),
        "request-hash",
        "resource-id",
        datetime.now(timezone.utc),
    )
    assert reservation.outcome is ReservationOutcome.ACQUIRED
