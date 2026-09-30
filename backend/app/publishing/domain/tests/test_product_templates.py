"""The default product templates render into valid CloudFormation for every shared region."""

import pathlib

import assertpy
import jinja2
import pytest
import yaml
from jinja2.sandbox import SandboxedEnvironment

from app.publishing.domain.model import product_template

TEMPLATES = (
    pathlib.Path(__file__).parents[4] / "infra" / "backend" / "resources" / "product_publishing_app_stack" / "templates"
)
NAMES = ["workbench-template.yml", "virtual-target-template.yml"]


class _CfnTagLoader(yaml.SafeLoader):
    """Keeps short-form intrinsics as {"!Tag": value}, so references can be asserted."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {f"!{suffix}": loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {f"!{suffix}": loader.construct_sequence(node, deep=True)}
    return {f"!{suffix}": loader.construct_mapping(node, deep=True)}


_CfnTagLoader.add_multi_constructor("!", _tag)


def _render(name: str, ami_ids: dict | None = None, architecture: str | None = None) -> dict:
    rendered = (
        SandboxedEnvironment(loader=jinja2.BaseLoader())
        .from_string((TEMPLATES / name).read_text())
        .render(
            product_name="Test",
            product_version="1.0.0",
            ami_ids=ami_ids or {"us-east-1": "ami-0east"},
            architecture=architecture,
        )
    )
    return yaml.load(rendered, Loader=_CfnTagLoader)


@pytest.mark.parametrize("name", NAMES)
def test_region_map_lists_every_shared_region(name):
    template = _render(name, {"eu-north-1": "ami-0north", "us-east-1": "ami-0east"})

    assertpy.assert_that(template["Mappings"]["RegionMap"]).is_equal_to(
        {"eu-north-1": {"AMIId": "ami-0north"}, "us-east-1": {"AMIId": "ami-0east"}}
    )


@pytest.mark.parametrize("name", NAMES)
def test_drafts_render_for_validation(name):
    """The raw draft is Jinja, not YAML; CloudFormation rejects it ("YAML not well-formed"). Validation
    sees it rendered with a placeholder image."""
    raw = (TEMPLATES / name).read_text()
    with pytest.raises(yaml.YAMLError):
        yaml.load(raw, Loader=_CfnTagLoader)

    rendered = yaml.load(product_template.render_for_validation(raw), Loader=_CfnTagLoader)

    assertpy.assert_that(rendered["Mappings"]["RegionMap"]).is_equal_to(
        {product_template.VALIDATION_REGION_KEY: {"AMIId": product_template.VALIDATION_AMI_ID}}
    )


def test_plain_templates_pass_validation_rendering_unchanged():
    plain = "AWSTemplateFormatVersion: 2010-09-09\nResources:\n  Topic:\n    Type: AWS::SNS::Topic"

    assertpy.assert_that(product_template.render_for_validation(plain)).is_equal_to(plain)


def test_workbench_joins_the_spoke_workbench_group():
    """A connection gateway in the spoke is admitted to the spoke's workbench group (provisioning
    enablement pp-sg); without it the gateway cannot reach the workbench."""
    template = _render("workbench-template.yml")

    parameter = template["Parameters"]["WorkbenchSecurityGroupIdSSM"]
    assertpy.assert_that(parameter["Type"]).is_equal_to("AWS::SSM::Parameter::Value<AWS::EC2::SecurityGroup::Id>")
    assertpy.assert_that(parameter["Default"]).is_equal_to("/proserve/wb/provisioning-enablement/pp-sg")
    groups = template["Resources"]["Workbench"]["Properties"]["SecurityGroupIds"]
    assertpy.assert_that(groups).contains({"!Ref": "WorkbenchSecurityGroupIdSSM"}, {"!Ref": "UserSecurityGroupId"})


@pytest.mark.parametrize("name", NAMES)
def test_instance_role_cleanup_keeps_exactly_the_roles_own_policies(name):
    """CloudFormation cannot delete a role with a policy it did not attach (an SSM Quick Setup patch
    policy attaches one to every instance role). The cleanup strips everything not in its keep lists,
    so those lists must be exactly the role's own policies."""
    resources = _render(name)["Resources"]
    role = resources["InstanceRole"]["Properties"]
    cleanup = resources["InstanceRoleCleanup"]

    assertpy.assert_that(cleanup["Type"]).is_equal_to("Custom::InstanceRoleCleanup")
    assertpy.assert_that(cleanup["Properties"]["RoleName"]).is_equal_to({"!Ref": "InstanceRole"})
    assertpy.assert_that(cleanup["Properties"]["KeepManagedPolicyArns"]).is_equal_to(role["ManagedPolicyArns"])
    assertpy.assert_that(cleanup["Properties"]["KeepInlinePolicyNames"]).is_equal_to(
        [policy["PolicyName"] for policy in role["Policies"]]
    )


@pytest.mark.parametrize("name", NAMES)
def test_instance_role_cleanup_runs_after_the_instance_and_before_the_role(name):
    template = _render(name)
    resources = template["Resources"]

    # CloudFormation deletes a resource before the ones it depends on: instance, cleanup, role.
    assertpy.assert_that(resources["Workbench"]["DependsOn"]).is_equal_to("InstanceRoleCleanup")
    assertpy.assert_that(resources["InstanceRoleCleanup"]["Properties"]["ServiceToken"]).is_equal_to(
        {"!Ref": "RoleCleanupFunctionArnSSM"}
    )
    parameter = template["Parameters"]["RoleCleanupFunctionArnSSM"]
    assertpy.assert_that(parameter["Type"]).is_equal_to("AWS::SSM::Parameter::Value<String>")
    assertpy.assert_that(parameter["Default"]).is_equal_to(
        "/proserve/wb/provisioning-enablement/workbench-role-cleanup-function-arn"
    )
