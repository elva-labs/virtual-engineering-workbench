import logging
from unittest import mock

import assertpy
import pytest

from app.publishing.entrypoints.api import bootstrapper, config
from app.shared.adapters.unit_of_work_v2 import dynamodb_migrations


def test_bootstrapper():
    # ARRANGE
    app_config = config.AppConfig(**config.config)

    # ACT
    dependencies = bootstrapper.bootstrap(app_config=app_config, logger=logging.getLogger())

    # ASSERT
    assertpy.assert_that(dependencies).is_not_none()


@pytest.mark.migrations
def test_bootstrapper_runs_the_publishing_migrations(monkeypatch):
    migrate = mock.Mock()
    monkeypatch.setattr(dynamodb_migrations.DynamoDBMigrator, "migrate", migrate)

    bootstrapper.bootstrap(app_config=config.AppConfig(**config.config), logger=logging.getLogger())

    migrate.assert_called_once()


@pytest.mark.migrations
def test_bootstrapper_fails_fast_when_a_migration_fails(monkeypatch):
    """A failed migration must stop the API from starting: it would otherwise read versions still under
    the old keys."""
    monkeypatch.setattr(
        dynamodb_migrations.DynamoDBMigrator, "migrate", mock.Mock(side_effect=RuntimeError("migration failed"))
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        bootstrapper.bootstrap(app_config=config.AppConfig(**config.config), logger=logging.getLogger())
