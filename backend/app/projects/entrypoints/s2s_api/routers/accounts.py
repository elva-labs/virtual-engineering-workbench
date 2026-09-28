import re
from datetime import datetime, timezone
from http import HTTPStatus
from uuid import uuid4

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.projects.domain.command_handlers.internal import string_utils
from app.projects.domain.commands.project_accounts import (
    deactivate_project_account_s2s_command,
    on_board_project_account_command,
    update_project_account_command,
)
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.value_objects import (
    account_description_value_object,
    account_id_value_object,
    account_name_value_object,
    account_technology_id_value_object,
    account_type_value_object,
    aws_account_id_value_object,
    project_id_value_object,
    region_value_object,
)
from app.projects.entrypoints.s2s_api import bootstrapper, common, idempotency, s2s_exception
from app.projects.entrypoints.s2s_api.model import api_model

tracer = Tracer()

READ_SCOPE = "clients/projects/account.read"
WRITE_SCOPE = "clients/projects/account.write"
_AWS_REGION_FORMAT = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-\d$")


def _account_response(
    account: project_account.ProjectAccount,
) -> api_model.ProjectAccount:
    # Workflow error text can include infrastructure names and downstream details.
    safe_error = string_utils.safe_onboarding_error(account.lastOnboardingErrorMessage)
    return api_model.ProjectAccount(
        accountId=account.id,
        projectId=account.projectId,
        awsAccountId=account.awsAccountId,
        accountType=str(account.accountType),
        name=account.accountName,
        description=account.accountDescription,
        technologyId=account.technologyId,
        stage=str(account.stage),
        region=account.region,
        status=(str(account.accountStatus) if account.accountStatus is not None else None),
        lastOnboardingResult=(str(account.lastOnboardingResult) if account.lastOnboardingResult is not None else None),
        lastOnboardingError=safe_error,
        createDate=account.createDate,
        lastUpdateDate=account.lastUpdateDate,
    )


def _accepted(account_id: str) -> idempotency.StoredCreateResponse:
    return idempotency.StoredCreateResponse(HTTPStatus.ACCEPTED, {"accountId": account_id})


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/accounts")
    def list_project_accounts(project_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        try:
            accounts = dependencies.projects_query_service.list_project_accounts(project_id)
        except Exception as error:
            raise s2s_exception.ResourceReadNotReady() from error
        return api_model.ProjectAccountPage(accounts=[_account_response(item) for item in accounts])

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/accounts/<account_id>")
    def get_project_account(project_id: str, account_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        try:
            account = dependencies.projects_query_service.get_project_account_by_id(project_id, account_id)
        except Exception as error:
            raise s2s_exception.ResourceReadNotReady() from error
        if account is None or account.projectId != project_id:
            raise s2s_exception.ResourceNotFound()
        return _account_response(account)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.post("/projects/<project_id>/accounts")
    def create_project_account(project_id: str, request: api_model.CreateProjectAccountRequest):
        client = common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        scope = common.idempotency_scope(router, client, project_id, "CREATE_PROJECT_ACCOUNT")

        # Validate the externally supplied values before reaching domain handlers.
        account_id = aws_account_id_value_object.from_str(request.awsAccountId)
        account_type = account_type_value_object.from_str(request.accountType)
        account_name = account_name_value_object.from_str(request.name)
        account_description = account_description_value_object.from_str(request.description)
        technology_id = account_technology_id_value_object.from_str(request.technologyId)
        project_id_value = project_id_value_object.from_str(project_id)
        if not _AWS_REGION_FORMAT.fullmatch(request.region):
            raise s2s_exception.InvalidRequest()
        try:
            region = region_value_object.from_str(request.region)
        except domain_exception.DomainException as error:
            raise s2s_exception.InvalidRequest() from error
        stage = project_account.ProjectAccountStageEnum(request.stage)

        try:
            assigned_accounts = dependencies.projects_query_service.list_project_accounts_by_aws_account(
                request.awsAccountId
            )
        except Exception as error:
            raise s2s_exception.ResourceReadNotReady() from error
        retained_inactive_accounts = [
            account
            for account in assigned_accounts
            if account.projectId == project_id
            and account.accountStatus == project_account.ProjectAccountStatusEnum.Inactive
        ]
        reserved_account_id = (
            retained_inactive_accounts[0].id
            if len(assigned_accounts) == 1 and len(retained_inactive_accounts) == 1
            else str(uuid4())
        )

        def make_command(resource_id: str):
            return on_board_project_account_command.OnBoardProjectAccountCommand(
                account_id=account_id,
                account_type=account_type,
                account_name=account_name,
                account_description=account_description,
                project_id=project_id_value,
                stage=stage,
                technology=technology_id,
                region=region,
                reserved_account_id=resource_id,
            )

        def create(resource_id: str) -> idempotency.StoredCreateResponse:
            try:
                technology = dependencies.technologies_query_service.get_technology_by_id(
                    project_id, request.technologyId
                )
            except Exception as error:
                raise s2s_exception.ResourceReadNotReady() from error
            if technology is None or technology.project_id != project_id:
                raise s2s_exception.InvalidRequest()

            try:
                assignments = dependencies.projects_query_service.list_project_accounts_by_aws_account(
                    request.awsAccountId
                )
                current_accounts = dependencies.projects_query_service.list_project_accounts(
                    project_id,
                    account_type=request.accountType,
                    stage=request.stage,
                    technology_id=request.technologyId,
                )
            except Exception as error:
                raise s2s_exception.ResourceReadNotReady() from error

            if assignments:
                if (
                    len(assignments) == 1
                    and assignments[0].projectId == project_id
                    and assignments[0].accountStatus == project_account.ProjectAccountStatusEnum.Inactive
                ):
                    if assignments[0].id != resource_id:
                        raise s2s_exception.ResourceReadNotReady()
                else:
                    raise s2s_exception.ResourceConflict()

            duplicate_configuration = any(
                item.region == request.region
                and item.accountStatus != project_account.ProjectAccountStatusEnum.Inactive
                for item in current_accounts
            )
            if duplicate_configuration:
                raise s2s_exception.ResourceConflict()

            dependencies.command_bus.handle(make_command(resource_id))
            return _accepted(resource_id)

        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=reserved_account_id,
            resource_exists=lambda resource_id: dependencies.projects_query_service.get_project_account_by_id(
                project_id, resource_id
            )
            is not None,
            response_for_id=_accepted,
            create=create,
            now=datetime.now(timezone.utc),
            resume_existing=lambda resource_id: dependencies.command_bus.handle(make_command(resource_id)),
        )
        return api_gateway.Response(
            status_code=result.status_code,
            body=api_model.CreateProjectAccountResponse.model_validate(result.body),
            headers={**common.NO_STORE, "Retry-After": "5"},
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/accounts/<account_id>")
    def update_project_account(
        project_id: str,
        account_id: str,
        request: api_model.UpdateProjectAccountRequest,
    ):
        common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        existing = _read_account(dependencies, project_id, account_id)
        if existing is None or existing.projectId != project_id:
            raise s2s_exception.ResourceNotFound()

        try:
            technology = dependencies.technologies_query_service.get_technology_by_id(project_id, request.technologyId)
        except Exception as error:
            raise s2s_exception.ResourceReadNotReady() from error
        if technology is None or technology.project_id != project_id:
            raise s2s_exception.InvalidRequest()

        if not _AWS_REGION_FORMAT.fullmatch(request.region):
            raise s2s_exception.InvalidRequest()
        try:
            region = region_value_object.from_str(request.region)
        except domain_exception.DomainException as error:
            raise s2s_exception.InvalidRequest() from error

        command = update_project_account_command.UpdateProjectAccountCommand(
            project_id=project_id_value_object.from_str(project_id),
            account_id=account_id_value_object.from_str(account_id),
            account_name=account_name_value_object.from_str(request.name),
            account_description=account_description_value_object.from_str(request.description),
            account_type=account_type_value_object.from_str(request.accountType),
            technology=account_technology_id_value_object.from_str(request.technologyId),
            stage=project_account.ProjectAccountStageEnum(request.stage),
            region=region,
        )
        try:
            dependencies.command_bus.handle(command)
        except domain_exception.DomainException:
            raise
        except Exception as error:
            # Publication can fail after the handler has stored a pending operation.
            # A retry of this PUT lets the handler resume that operation.
            raise s2s_exception.ResourceReadNotReady() from error

        updated = _read_account(dependencies, project_id, account_id)
        if updated is None or updated.projectId != project_id:
            raise s2s_exception.ResourceReadNotReady()
        if updated.accountStatus in (
            project_account.ProjectAccountStatusEnum.OnBoarding,
            project_account.ProjectAccountStatusEnum.ReOnboarding,
        ):
            return api_gateway.Response(
                status_code=HTTPStatus.ACCEPTED,
                body=api_model.CreateProjectAccountResponse(accountId=updated.id),
                headers={**common.NO_STORE, "Retry-After": "5"},
                content_type=content_types.APPLICATION_JSON,
            )
        if updated.accountStatus != project_account.ProjectAccountStatusEnum.Active:
            raise s2s_exception.ResourceConflict()
        return api_gateway.Response(
            status_code=HTTPStatus.OK,
            body=_account_response(updated),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/accounts/<account_id>")
    def deactivate_project_account(project_id: str, account_id: str):
        common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        command = deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand(
            project_id=project_id_value_object.from_str(project_id),
            account_id=account_id_value_object.from_str(account_id),
        )
        try:
            dependencies.command_bus.handle(command)
        except domain_exception.ProjectAccountStateConflict as error:
            raise s2s_exception.ResourceConflict() from error
        return api_gateway.Response(
            status_code=HTTPStatus.NO_CONTENT,
            headers=common.NO_STORE,
        )

    return router


def _read_account(dependencies, project_id: str, account_id: str):
    try:
        return dependencies.projects_query_service.get_project_account_by_id(project_id, account_id)
    except Exception as error:
        raise s2s_exception.ResourceReadNotReady() from error
