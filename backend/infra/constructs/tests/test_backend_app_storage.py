import aws_cdk
from aws_cdk.assertions import Template

from infra import config
from infra.constructs import backend_app_storage


def test_backend_app_storage_enables_expire_date_ttl_when_requested():
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "StorageTest")
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="111111111111",
        image_service_account="222222222222",
        catalog_service_account="333333333333",
        component_name="packaging",
        environment_config=config.env_config["dev"],
        component_specific=config.packaging_app_config["dev"],
    )

    backend_app_storage.BackendAppStorage(stack, "Storage", app_config, enable_ttl=True)

    Template.from_stack(stack).has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TimeToLiveSpecification": {
                "AttributeName": "ExpireDate",
                "Enabled": True,
            }
        },
    )
