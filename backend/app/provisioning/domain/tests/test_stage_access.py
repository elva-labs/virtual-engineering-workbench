import pytest

from app.provisioning.domain.model import stage_access


@pytest.mark.parametrize(
    "roles,expected",
    [
        (["PLATFORM_USER"], ["PROD"]),
        (["SUPPORT"], ["PROD"]),
        ([], ["PROD"]),
        (["BETA_USER"], ["QA", "PROD"]),
        (["PLATFORM_USER", "BETA_USER"], ["QA", "PROD"]),
        (["PRODUCT_CONTRIBUTOR"], ["DEV", "QA", "PROD"]),
        (["POWER_USER"], ["DEV", "QA", "PROD"]),
        (["PROGRAM_OWNER"], ["DEV", "QA", "PROD"]),
        (["ADMIN", "PLATFORM_USER"], ["DEV", "QA", "PROD"]),
    ],
)
def test_allowed_stages_follow_the_role(roles, expected):
    assert stage_access.allowed_stages(roles) == expected


def test_is_allowed_accepts_any_stage_casing():
    assert stage_access.is_allowed(["PLATFORM_USER"], "prod")
    assert not stage_access.is_allowed(["PLATFORM_USER"], "dev")
