"""Permissions granted to the Projects service-to-service API Lambda."""

from aws_cdk import aws_iam


def permissions(projects_table, event_bus, audit_logging_key_arn: str, cognito_user_pool_arn: str):
    return [
        lambda lambda_function: lambda_function.add_to_role_policy(
            aws_iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                effect=aws_iam.Effect.ALLOW,
                resources=[audit_logging_key_arn],
            )
        ),
        lambda lambda_function: lambda_function.add_to_role_policy(
            aws_iam.PolicyStatement(
                actions=["cognito-idp:ListUsers"],
                effect=aws_iam.Effect.ALLOW,
                resources=[cognito_user_pool_arn],
            )
        ),
        lambda lambda_function: projects_table.grant_read_write_data(lambda_function),
        lambda lambda_function: event_bus.grant_put_events_to(lambda_function),
    ]
