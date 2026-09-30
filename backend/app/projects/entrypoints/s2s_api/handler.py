from http import HTTPStatus

from aws_lambda_powertools import logging, tracing
from aws_lambda_powertools.event_handler import api_gateway
from aws_lambda_powertools.event_handler.exceptions import NotFoundError
from aws_lambda_powertools.event_handler.middlewares.openapi_validation import RequestValidationError
from aws_lambda_powertools.utilities import typing
from aws_xray_sdk.core import patch_all

from app.projects.domain.exceptions import domain_exception
from app.projects.entrypoints.s2s_api import bootstrapper, config, problem_details, s2s_exception
from app.projects.entrypoints.s2s_api.routers import (
    accounts,
    assignments,
    enrolments,
    groups,
    projects,
    service_clients,
    technologies,
)
from app.shared.logging.helpers import clear_auth_headers
from app.shared.middleware import authorization, exception_handler
from app.shared.middleware.metric import metric_handlers
from app.shared.middleware.metric.types import MetricDimensionNames

patch_all()

logger = logging.Logger()
tracer = tracing.Tracer()

app_config = config.AppConfig(**config.config)
default_region_name = app_config.get_default_region()
secret_name = app_config.get_audit_logging_key_name()

dependencies = bootstrapper.bootstrap(app_config, logger)

cors_config = api_gateway.CORSConfig(**app_config.cors_config)
app = api_gateway.APIGatewayRestResolver(
    cors=cors_config,
    strip_prefixes=app_config.get_strip_prefixes(),
    enable_validation=True,
)
app.use(middlewares=[authorization.require_auth_context])
app.include_router(assignments.init(dependencies=dependencies))
app.include_router(accounts.init(dependencies=dependencies))
app.include_router(projects.init(dependencies=dependencies))
app.include_router(enrolments.init(dependencies=dependencies))
app.include_router(service_clients.init(dependencies=dependencies))
app.include_router(groups.init(dependencies=dependencies))
app.include_router(technologies.init(dependencies=dependencies))


def _request_id() -> str | None:
    return app.current_event.raw_event.get("requestContext", {}).get("requestId")


@app.exception_handler(s2s_exception.S2SException)
def handle_s2s_error(error: s2s_exception.S2SException):
    return problem_details.api_response(error, _request_id())


@app.exception_handler(RequestValidationError)
def handle_validation_error(error: RequestValidationError):
    return problem_details.api_response(s2s_exception.InvalidRequest(), _request_id())


@app.exception_handler(NotFoundError)
def handle_not_found(error: NotFoundError):
    return problem_details.api_response(s2s_exception.ResourceNotFound(), _request_id())


@app.exception_handler(domain_exception.DomainException)
def handle_domain_error(error: domain_exception.DomainException):
    problem = problem_details.response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        detail="The request failed Projects validation.",
        code="DOMAIN_VALIDATION_FAILED",
        request_id=_request_id(),
        retryable=False,
    )
    return api_gateway.Response(
        status_code=problem["statusCode"],
        body=problem["body"],
        headers=problem["headers"],
        content_type=problem_details.PROBLEM_CONTENT_TYPE,
    )


@app.exception_handler(authorization.AuthException)
def handle_auth_error(error: authorization.AuthException):
    return problem_details.api_response(s2s_exception.Unauthorized(), _request_id())


@app.exception_handler(Exception)
def handle_unexpected_error(error: Exception):
    logger.error("Unhandled Projects S2S API error", exceptionClass=type(error).__name__)
    return problem_details.api_response(s2s_exception.S2SException(), _request_id())


@tracer.capture_lambda_handler  # type: ignore
@logger.inject_lambda_context  # type: ignore
@exception_handler.handle_exceptions(user_exceptions=[domain_exception.DomainException], cors_config=cors_config)
@metric_handlers.report_invocation_metrics(
    dimensions={MetricDimensionNames.ByAPI: "RestAPI"},
    enable_audit=True,
    region_name=default_region_name,
    secret_name=secret_name,
)
def handler(
    event: dict,
    context: typing.LambdaContext,
):
    logger.info(clear_auth_headers(event, mask_body=True))
    return app.resolve(event, context)
