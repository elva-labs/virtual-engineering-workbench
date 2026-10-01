import os

from pydantic import BaseModel


class AppConfig(BaseModel):
    def get_default_region(self) -> str:
        return os.environ.get("AWS_DEFAULT_REGION")

    def get_table_name(self) -> str:
        return os.environ.get("TABLE_NAME", "")

    def get_domain_event_bus_name(self) -> str:
        return os.environ.get("DOMAIN_EVENT_BUS_ARN", "")

    def get_bounded_context_name(self) -> str:
        return os.environ.get("BOUNDED_CONTEXT", "")

    def get_image_service_role(self) -> str:
        return os.environ.get("IMAGE_SERVICE_ROLE", "")

    def get_image_service_aws_account_id(self) -> str:
        return os.environ.get("IMAGE_SERVICE_AWS_ACCOUNT_ID", "")

    def get_image_service_key_name(self) -> str:
        return os.environ.get("IMAGE_SERVICE_KEY_NAME", "")

    def get_image_distribution_mode(self) -> str:
        """ "share" (default) or "store-restore" (env config "image-distribution")."""
        return os.environ.get("IMAGE_DISTRIBUTION_MODE", "share")

    def get_image_import_role(self) -> str:
        """Role in each target account that restores distributed images ("store-restore")."""
        return os.environ.get("IMAGE_IMPORT_ROLE", "")

    def get_image_import_bucket_prefix(self) -> str:
        """Target accounts' import buckets are named <prefix>-<account>-<region> ("store-restore")."""
        return os.environ.get("IMAGE_IMPORT_BUCKET_PREFIX", "")

    def get_store_with_function_role(self) -> bool:
        return os.environ.get("IMAGE_STORE_WITH_FUNCTION_ROLE", "false").lower() == "true"

    def get_gsi_name_entities(self) -> str:
        return os.environ.get("GSI_NAME_ENTITIES", "")
