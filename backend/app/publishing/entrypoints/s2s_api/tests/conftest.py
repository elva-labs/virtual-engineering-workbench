import os
from types import SimpleNamespace

import pytest
from openapi_spec_validator.readers import read_from_filename

from app.shared.api import secrets_manager_api


@pytest.fixture(autouse=True)
def runtime_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AUDIT_LOGGING_KEY_NAME", "audit-key")
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "Tests")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "Publishing")
    monkeypatch.setattr(secrets_manager_api.SecretsManagerAPI, "get_secret_value", lambda self, secret_id: "key")


@pytest.fixture()
def lambda_context():
    return SimpleNamespace(
        function_name="test",
        memory_limit_in_mb=128,
        invoked_function_arn="arn:aws:lambda:us-east-1:000000000:function:test",
        aws_request_id="lambda-request-1",
    )


@pytest.fixture()
def client_event():
    def build(method, path, body=None, headers=None, client_id="client-1", scopes=None):
        request_headers = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
        return {
            "resource": path,
            "path": path,
            "httpMethod": method,
            "headers": request_headers,
            "multiValueHeaders": {key: [value] for key, value in request_headers.items()},
            "queryStringParameters": None,
            "multiValueQueryStringParameters": None,
            "pathParameters": {},
            "stageVariables": None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "sub": client_id,
                        "client_id": client_id,
                        "token_use": "access",
                        "scope": " ".join(scopes or []),
                    }
                },
                "resourcePath": path,
                "httpMethod": method,
                "path": path,
                "accountId": "111111111111",
                "stage": "test",
                "requestId": "request-1",
                "identity": {"sourceIp": "0.0.0.0", "userAgent": "service-client"},
                "apiId": "api",
            },
            "body": body,
            "isBase64Encoded": False,
        }

    return build


@pytest.fixture
def api_schema():
    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "schema", "proserve-workbench-s2s-publishing-api-schema.yaml"
    )
    spec_dict, _ = read_from_filename(schema_path)
    return spec_dict
