import os

from pydantic import Field

from app.shared.config import VEWBaseConfig


class AppConfig(VEWBaseConfig):
    cors_config: dict = Field(default_factory=dict)

    def get_api_base_path(self) -> str:
        return f'/{os.environ.get("API_BASE_PATH", "clients/publishing")}'

    def get_strip_prefixes(self) -> list[str]:
        return [f"/{value.strip()}" for value in os.environ.get("STRIP_PREFIXES", "").split(",") if value.strip()]

    def get_audit_logging_key_name(self) -> str:
        return os.environ.get("AUDIT_LOGGING_KEY_NAME", "")

    def get_domain_event_bus_name(self) -> str:
        return os.environ.get("DOMAIN_EVENT_BUS_ARN", "")

    def get_table_name(self) -> str:
        return os.environ.get("TABLE_NAME", "")

    def get_gsi_name_entities(self) -> str:
        return os.environ.get("GSI_NAME_ENTITIES", "")


config = {
    "cors_config": {
        "allow_origin": "*",
        "expose_headers": ["Retry-After"],
        "allow_headers": ["Content-Type,X-Amz-Date,Authorization,X-Api-Key,x-amz-security-token,Idempotency-Key"],
        "max_age": 100,
        "allow_credentials": True,
    },
}
