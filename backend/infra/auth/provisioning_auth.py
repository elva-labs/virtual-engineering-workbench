from infra import config
from infra.auth import provisioning_auth_schema, shared_auth_schema
from infra.constructs import backend_app_api_auth

provisioning_bc_auth_policies: list[backend_app_api_auth.CedarPolicy] = [
    backend_app_api_auth.CedarPolicy(
        description="Allows admins to Get Features and Product IP Mappings as well as to Update these details",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectCapacityWorkbenches,
                    provisioning_auth_schema.ProvisioningBCActions.GetFeatures,
                    provisioning_auth_schema.ProvisioningBCActions.GetProductsIPMappings,
                    provisioning_auth_schema.ProvisioningBCActions.UpdateFeatures,
                    provisioning_auth_schema.ProvisioningBCActions.UpdateProductsIPMappings,
                ])},
                resource
            )
            when {{ principal in {config.CedarResourceAttribute.ADMINS} }};
    """,
    ),
    backend_app_api_auth.CedarPolicy(
        description="Allows program owners to Get Projects data as well as Stop provisioned Products.",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    # Program admins see their own program's capacity.
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectCapacity,
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectCapacityWorkbenches,
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectPaginatedProvisionedProducts,
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectProvisionedProducts,
                    provisioning_auth_schema.ProvisioningBCActions.RemoveProvisionedProducts,
                    provisioning_auth_schema.ProvisioningBCActions.StopProvisionedProducts,
                ])},
                resource
            )
            when {{ principal in {config.CedarResourceAttribute.PROGRAM_OWNERS} }};
    """,
    ),
    backend_app_api_auth.CedarPolicy(
        description="Allows users to get available Provisioned Products and Available Products with the ability to alter state of the products.",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    provisioning_auth_schema.ProvisioningBCActions.GetAvailableProducts,
                    provisioning_auth_schema.ProvisioningBCActions.AuthorizeUserIpAddress,
                    provisioning_auth_schema.ProvisioningBCActions.GetAvailableProducts,
                    provisioning_auth_schema.ProvisioningBCActions.GetAvailableProductVersions,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProduct,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProductActivities,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProductLifecycle,
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectCapacity,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProducts,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProductSSHKey,
                    provisioning_auth_schema.ProvisioningBCActions.GetProvisionedProductUserCredentials,
                    provisioning_auth_schema.ProvisioningBCActions.LaunchProduct,
                    provisioning_auth_schema.ProvisioningBCActions.RemoveProvisionedProduct,
                    provisioning_auth_schema.ProvisioningBCActions.StartProvisionedProduct,
                    provisioning_auth_schema.ProvisioningBCActions.StopProvisionedProduct,
                    provisioning_auth_schema.ProvisioningBCActions.UpdateProvisionedProduct,
                    provisioning_auth_schema.ProvisioningBCActions.UpdateProvisionedProductLifecycle,
                ])},
                resource
            )
            when {{ principal in {config.CedarResourceAttribute.PLATFORM_USERS} }};
    """,
    ),
    backend_app_api_auth.CedarPolicy(
        description="Allows SUPPORT (and ADMIN) to list a project's provisioned products, if the project allows remote support.",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectPaginatedProvisionedProducts,
                    provisioning_auth_schema.ProvisioningBCActions.GetProjectProvisionedProducts,
                ])},
                resource
            )
            when {{ principal in {config.CedarResourceAttribute.SUPPORTERS} && {config.CEDAR_REMOTE_SUPPORT_ENABLED} }};
    """,
    ),
    backend_app_api_auth.CedarPolicy(
        # Not under a program, so no project entity: platform admins are counted by the authorizer as
        # ADMIN somewhere (totalAdminAssignments), as CreateProject requires.
        description="Allows platform admins to view every spoke's capacity and request more quota.",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    provisioning_auth_schema.ProvisioningBCActions.GetCapacityOverview,
                    provisioning_auth_schema.ProvisioningBCActions.RequestQuotaIncrease,
                ])},
                resource
            )
            when {{ principal has {shared_auth_schema.SharedAttributes.TotalAdminAssignments} && principal.{shared_auth_schema.SharedAttributes.TotalAdminAssignments} > 0 }};
    """,
    ),
    backend_app_api_auth.CedarPolicy(
        description="Allows all authenticated principals to get Swagger API spec.",
        statement=f"""
            permit (
                principal,
                action in {provisioning_auth_schema.get_full_action_names([
                    provisioning_auth_schema.ProvisioningBCActions.GetSwaggerSpec,
                    provisioning_auth_schema.ProvisioningBCActions.GetUserProfile,
                    provisioning_auth_schema.ProvisioningBCActions.UpdateUserProfile,
                ])},
                resource
            );
    """,
    ),
]
