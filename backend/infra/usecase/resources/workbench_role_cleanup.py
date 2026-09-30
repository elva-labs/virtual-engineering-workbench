"""Custom resource handler: strip foreign policies from a workbench instance role before deletion.

CloudFormation deletes an AWS::IAM::Role only after detaching the policies its own template
declares; anything attached from outside makes the delete fail ("must detach all policies first")
and leaves the workbench in PROVISIONING_ERROR. An organization's SSM Quick Setup patch policy, for
example, attaches AWSQuickSetupPatchPolicyBaselineAccess to the role of every managed instance, so
every workbench removal fails. On Delete this handler detaches managed and deletes inline
policies that are not in the template's keep lists; Create and Update do nothing.

Deployed inline (ZipFile), so it uses only the standard library and boto3.
"""

import json
import urllib.request

import boto3

iam = boto3.client("iam")


def _send(event, context, status, reason=""):
    body = json.dumps(
        {
            "Status": status,
            "Reason": (reason or f"See log stream {context.log_stream_name}")[:1024],
            "PhysicalResourceId": event.get("PhysicalResourceId") or event["LogicalResourceId"],
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": {},
        }
    ).encode()
    request = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT", headers={"Content-Type": "", "Content-Length": str(len(body))}
    )
    urllib.request.urlopen(request, timeout=30)


def strip_foreign_policies(role_name, keep_managed, keep_inline, client=iam):  # noqa: C901
    """Remove every policy on role_name that its template does not declare. Returns what was removed."""
    removed = []
    try:
        for page in client.get_paginator("list_attached_role_policies").paginate(RoleName=role_name):
            for policy in page["AttachedPolicies"]:
                if policy["PolicyArn"] not in keep_managed:
                    client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
                    removed.append(policy["PolicyArn"])
        for page in client.get_paginator("list_role_policies").paginate(RoleName=role_name):
            for name in page["PolicyNames"]:
                if name not in keep_inline:
                    client.delete_role_policy(RoleName=role_name, PolicyName=name)
                    removed.append(name)
    except client.exceptions.NoSuchEntityException:
        pass  # the role (or a policy) is already gone: nothing left to block the delete
    return removed


def handler(event, context):
    status, reason = "SUCCESS", ""
    try:
        if event["RequestType"] == "Delete":
            props = event["ResourceProperties"]
            removed = strip_foreign_policies(
                props["RoleName"],
                set(props.get("KeepManagedPolicyArns", [])),
                set(props.get("KeepInlinePolicyNames", [])),
            )
            print(json.dumps({"role": props["RoleName"], "removed": removed}))
    except Exception as error:  # noqa: BLE001 - a failed cleanup must not block the stack delete forever
        # Report success anyway: the role delete then fails with CloudFormation's own, clearer error
        # (detach the foreign policies by hand and retry), instead of the stack
        # hanging on a custom resource that can never succeed.
        print(json.dumps({"error": repr(error)}))
        reason = f"cleanup failed: {error!r}"
    _send(event, context, status, reason)
