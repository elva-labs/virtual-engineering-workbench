from unittest.mock import Mock

from infra.backend.projects_s2s_permissions import permissions


def test_projects_s2s_permissions_are_scoped_to_its_dependencies():
    table = Mock()
    event_bus = Mock()
    lambda_function = Mock()
    audit_key_arn = "arn:aws:secretsmanager:eu-west-1:111111111111:secret:audit"
    user_pool_arn = "arn:aws:cognito-idp:eu-west-1:111111111111:userpool/pool"

    for grant in permissions(table, event_bus, audit_key_arn, user_pool_arn):
        grant(lambda_function)

    table.grant_read_write_data.assert_called_once_with(lambda_function)
    event_bus.grant_put_events_to.assert_called_once_with(lambda_function)
    policy_statements = [call.args[0].to_statement_json() for call in lambda_function.add_to_role_policy.call_args_list]
    assert policy_statements == [
        {
            "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
            "Effect": "Allow",
            "Resource": audit_key_arn,
        },
        {
            "Action": "cognito-idp:ListUsers",
            "Effect": "Allow",
            "Resource": user_pool_arn,
        },
    ]
