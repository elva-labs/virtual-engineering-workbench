import json
import re

ONBOARDING_ERROR_MESSAGES = {
    "TaskFailedToStart": "Onboarding task failed to start.",
    "EssentialContainerExited": "An onboarding container exited unexpectedly.",
    "States.Timeout": "Onboarding timed out.",
    "States.TaskFailed": "An onboarding task failed.",
    "States.Permissions": "Onboarding lacked required permissions.",
}
DEFAULT_ONBOARDING_ERROR_MESSAGE = (
    "Account onboarding failed. Consult VEW onboarding logs for details."
)


def sanitize_aws_resource_ids(text: str) -> str:

    patterns = [
        r"\b\d{12}\b",  # AWS Account IDs (12 digits)
        r"vpc-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # VPC IDs
        r"subnet-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # Subnet IDs
        r"arn:(?:aws|aws-cn|aws-us-gov):[\w-]+:[\w-]*:(?:\d{12})?:[\w-]+[:/][\w-]+(?:[:/][\w-]+)*",  # ARN
        r"ami-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # AMI IDs
        r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # EC2 Instance IDs
        r"vol-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # EBS Volume IDs
        r"sg-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # Security Group IDs
        r"igw-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # Internet Gateway IDs
        r"acl-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # Network ACL IDs
        r"rtb-[0-9a-f]{8}(?:[0-9a-f]{9})?",  # Route Table IDs
    ]

    combined_pattern = "|".join(f"({pattern})" for pattern in patterns)

    return re.sub(combined_pattern, "[REDACTED]", text)


def try_parse_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def safe_onboarding_error(error: str | None, cause: str | None = None) -> str | None:
    """Return a fixed operator message without propagating workflow error detail.

    Pass both Step Functions error and cause when handling a new failure. Pass a
    previously stored message alone when rendering old account records.
    """
    if not error and not cause:
        return None

    if cause:
        cause_value = try_parse_json(cause)
        stop_code = (
            cause_value.get("StopCode") if isinstance(cause_value, dict) else None
        )
        if isinstance(stop_code, str) and stop_code in ONBOARDING_ERROR_MESSAGES:
            return ONBOARDING_ERROR_MESSAGES[stop_code]

    if error in ONBOARDING_ERROR_MESSAGES:
        return ONBOARDING_ERROR_MESSAGES[error]

    if error:
        for code, safe_message in ONBOARDING_ERROR_MESSAGES.items():
            if error == safe_message or error.startswith(f"{code}:"):
                return safe_message

    return DEFAULT_ONBOARDING_ERROR_MESSAGE
