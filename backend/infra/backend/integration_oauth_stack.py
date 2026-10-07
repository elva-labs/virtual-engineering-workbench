import aws_cdk
import constructs
from aws_cdk import aws_cognito, aws_ssm

from infra import config
from infra.constructs import backend_app_api_oauth_client, backend_app_api_resource_server


class IntegrationOauthStack(aws_cdk.Stack):
    def __init__(
        self,
        scope: constructs.Construct,
        id: str,
        app_config: config.AppConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        user_pool_id = aws_ssm.StringParameter.value_for_string_parameter(
            self,
            app_config.environment_config["cognito-userpool-id-ssm-param"].format(environment=app_config.environment),
        )

        user_pool = aws_cognito.UserPool.from_user_pool_id(self, "BackendAppUserPool", user_pool_id)

        provisioning_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "ProvisioningResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/provisioning",
                scopes={
                    "product.read": "Read access to product catalogue",
                    "provisioned_product.write": "Allows to provision and manipulate provisioned products",
                    "provisioned_product.read": "Allows to get information about provisioned products",
                },
            ),
        )

        provisioning_compound_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "ProvisioningCompoundResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/provisioning-compound",
                scopes={
                    "provisioned_product.write": "Allows to provision and manipulate provisioned products",
                    "provisioned_product.read": "Allows to get information about provisioned products",
                },
            ),
        )

        projects_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "ProjectsResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/projects",
                scopes={
                    "program.read": "Allows to read project data",
                    "program.write": "Allows to manage projects",
                    "group_assignment.read": "Allows to read project group assignments",
                    "group_assignment.write": "Allows to manage project group assignments",
                    "client_assignment.bootstrap": "Allows platform recovery of orphan projects",
                    "assignment.write": "Allows to enrol users to programs",
                    "assignment.read": "Allows to read user data in the projects",
                    "client_assignment.read": "Allows to read service-client project assignments",
                    "client_assignment.write": "Allows to manage service-client project assignments",
                    "technology.read": "Allows service clients to read project technologies",
                    "technology.write": "Allows service clients to manage project technologies",
                    "account.read": "Allows service clients to read project accounts",
                    "account.write": "Allows service clients to manage project accounts",
                },
            ),
        )

        packaging_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "PackagingResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/packaging",
                scopes={
                    "component.read": "Allows service clients to read components and component versions",
                    "component.write": "Allows service clients to manage components and component versions",
                    "component.release": "Allows service clients to release immutable component versions",
                    "recipe.read": "Allows service clients to read recipes and recipe versions",
                    "recipe.write": "Allows service clients to manage recipes and recipe versions",
                    "recipe.release": "Allows service clients to release immutable recipe versions",
                    "pipeline.read": "Allows service clients to read pipelines and image build status",
                    "pipeline.write": "Allows service clients to manage pipelines",
                    "pipeline.execute": "Allows service clients to start image builds",
                    "base_image.read": "Allows service clients to read the released base images",
                    "base_image.write": "Allows service clients to release base images (releasing project only)",
                    "mandatory_components_list.read": "Allows service clients to read the mandatory components lists",
                    "mandatory_components_list.write": (
                        "Allows service clients to manage the mandatory components lists (releasing project only)"
                    ),
                },
            ),
        )

        publishing_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "PublishingResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/publishing",
                scopes={
                    "product.read": "Allows service clients to read products",
                    "product.write": "Allows service clients to create, update and archive products",
                    "version.read": "Allows service clients to read product versions and their stages",
                    "version.promote": "Allows service clients to promote a product version to a stage",
                },
            ),
        )

        publishing_compound_resource_server = backend_app_api_resource_server.BackendAppApiResourceServer(
            self,
            "PublishingCompoundResourceServer",
            user_pool,
            resource_server=backend_app_api_resource_server.ResourceServer(
                identifier="clients/publishing-compound",
                scopes={
                    "product.read": "Allows to read compound product data",
                    "version.read": "Allows to read compound product version data",
                },
            ),
        )

        backend_app_api_oauth_client.BackendAppApiOAuthClient(
            self,
            "SampleS2SClient",
            app_config=app_config,
            user_pool=user_pool,
            resource_servers=[
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=provisioning_resource_server,
                    scopes=["product.read", "provisioned_product.write", "provisioned_product.read"],
                ),
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=provisioning_compound_resource_server,
                    scopes=["provisioned_product.write", "provisioned_product.read"],
                ),
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=projects_resource_server,
                    scopes=[
                        "program.read",
                        "program.write",
                        "group_assignment.read",
                        "group_assignment.write",
                        "assignment.write",
                        "assignment.read",
                        "client_assignment.read",
                        "technology.read",
                        "technology.write",
                        "account.read",
                        "account.write",
                    ],
                ),
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=packaging_resource_server,
                    scopes=[
                        "component.read",
                        "component.write",
                        "component.release",
                        "recipe.read",
                        "recipe.write",
                        "recipe.release",
                        "pipeline.read",
                        "pipeline.write",
                        "pipeline.execute",
                        "base_image.read",
                        "base_image.write",
                        "mandatory_components_list.read",
                        "mandatory_components_list.write",
                    ],
                ),
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=publishing_resource_server,
                    scopes=["product.read", "product.write", "version.read", "version.promote"],
                ),
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=publishing_compound_resource_server,
                    scopes=["product.read", "version.read"],
                ),
            ],
            client_name="sample-s2s",
        )

        backend_app_api_oauth_client.BackendAppApiOAuthClient(
            self,
            "ProjectsAssignmentManagementClient",
            app_config=app_config,
            user_pool=user_pool,
            resource_servers=[
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=projects_resource_server,
                    scopes=["program.read", "program.write", "client_assignment.read", "client_assignment.write"],
                ),
            ],
            client_name="projects-assignment-management",
            client_construct_id="ProjectsAssignmentManagementOAuthClient",
        )

        backend_app_api_oauth_client.BackendAppApiOAuthClient(
            self,
            "PlatformProjectsBootstrapClient",
            app_config=app_config,
            user_pool=user_pool,
            resource_servers=[
                backend_app_api_oauth_client.AppClientResourceServer(
                    resource_server=projects_resource_server,
                    scopes=["client_assignment.read", "client_assignment.write", "client_assignment.bootstrap"],
                ),
            ],
            client_name="platform-projects-bootstrap",
            client_construct_id="PlatformProjectsBootstrapOAuthClient",
        )

        # backend_app_api_oauth_client.BackendAppApiOAuthClient(
        #     self,
        #     "AuthCodeFlowClient",
        #     app_config=app_config,
        #     user_pool=user_pool,
        #     resource_servers=[],
        #     client_name="auth-code-flow",
        #     custom_oauth_settings=aws_cognito.OAuthSettings(
        #         flows=aws_cognito.OAuthFlows(authorization_code_grant=True),
        #         scopes=[
        #             aws_cognito.OAuthScope.EMAIL,
        #             aws_cognito.OAuthScope.OPENID,
        #             aws_cognito.OAuthScope.PROFILE,
        #         ],
        #     ),
        #     generate_secret=False,
        # )
