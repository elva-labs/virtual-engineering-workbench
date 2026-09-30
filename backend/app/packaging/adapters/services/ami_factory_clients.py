from typing import Any, Callable

import boto3

from app.shared.api import sts_api

SESSION_USER = "ProductPackagingBaseImages"


def factory(
    admin_role: str, ami_factory_aws_account_id: str, region: str, boto_session: Any = None
) -> Callable[[str], Any]:
    """Clients in the AMI factory account, where images are built and parent images are resolved, through
    the packaging admin role (the same account and role as ParameterService)."""

    def client(service: str) -> Any:
        with sts_api.STSAPI(ami_factory_aws_account_id, region, admin_role, SESSION_USER, boto_session) as sts:
            access_key_id, secret_access_key, session_token = sts.get_temp_creds()
        return (boto_session or boto3).client(
            service,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
        )

    return client
