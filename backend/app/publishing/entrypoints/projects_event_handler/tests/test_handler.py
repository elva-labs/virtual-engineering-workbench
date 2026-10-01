def test_handler_project_account_on_boarded_event(
    mock_dependencies,
    generate_event,
    lambda_context,
    project_account_on_boarded_event_payload,
    mock_create_portfolio_command_handler,
    mock_distribute_platform_versions_command_handler,
):
    # ARRANGE
    from app.publishing.entrypoints.projects_event_handler import handler

    handler.dependencies = mock_dependencies
    event_bridge_event = generate_event(
        detail_type="ProjectAccountOnBoarded",
        detail=project_account_on_boarded_event_payload,
    )

    # ACT
    handler.handler(event_bridge_event, lambda_context)

    # ASSERT
    mock_create_portfolio_command_handler.assert_called_once()
    # docs/platform-products.md: the new portfolio also receives the platform versions already at its stage.
    mock_distribute_platform_versions_command_handler.assert_called_once()
