import typing
from urllib.parse import quote

from app.provisioning.adapters.exceptions import adapter_exception
from app.provisioning.adapters.internal import constants
from app.provisioning.domain.ports import projects_query_service
from app.provisioning.domain.read_models import project, project_account, project_assignment
from app.shared.api import aws_api, paging

# Every list is read to its last page: the scheduled jobs and launches need every program,
# not the first 100.
PAGE_SIZE = "100"


class ProjectsApiQueryService(projects_query_service.ProjectsQueryService):
    def __init__(
        self,
        api: aws_api.AWSAPI,
    ):
        self._aws_api = api

    def get_aws_accounts_by_status(
        self,
        project_id: str,
        statuses: list,
        account_type: typing.Optional[str] = None,
        stage: typing.Optional[str] = None,
    ) -> list[project_account.ProjectAccount]:
        accounts = self._get_accounts(project_id=project_id, account_type=account_type, account_stage=stage)
        try:
            project_accounts = [
                project_account.ProjectAccount(
                    id=pa["id"],
                    awsAccountId=pa["awsAccountId"],
                    stage=pa["stage"],
                    region=pa.get("region", None),
                )
                for pa in accounts
                if pa.get("accountStatus", "") in statuses
            ]
        except KeyError:
            raise adapter_exception.AdapterException(constants.NO_ACCOUNT_ASSIGNED)

        return project_accounts

    def get_aws_account_by_id(
        self,
        project_id: str,
        account_id: str,
    ) -> typing.Optional[project_account.ProjectAccount]:
        accounts = self._get_accounts(project_id=project_id)
        try:
            account = next(
                iter(
                    [
                        project_account.ProjectAccount(
                            id=pa["id"],
                            awsAccountId=pa["awsAccountId"],
                            stage=pa["stage"],
                            region=pa.get("region", None),
                        )
                        for pa in accounts
                        if pa["id"] == account_id
                    ]
                ),
                None,
            )
        except KeyError:
            raise adapter_exception.AdapterException(constants.NO_ACCOUNT_ASSIGNED)

        return account

    def get_projects(self) -> list[project.Project]:
        return [self._to_project(proj) for proj in self._iterate_projects()]

    def get_user_assignments_count(self, user_id: str) -> int:
        return sum(
            len(page.get("assignments") or [])
            for page in paging.iterate_pages(
                lambda query: self._aws_api.call_api(
                    path="internal/user/assignments", http_method="GET", query_params=query
                ),
                {"pageSize": PAGE_SIZE, "userId": user_id},
            )
        )

    def get_project(self, project_id: str) -> project.Project:
        # Stops at the page that holds the program: a launch doesn't need to read all of them.
        proj = next((proj for proj in self._iterate_projects() if proj.get("projectId") == project_id), None)
        if proj is None:
            raise adapter_exception.AdapterException(constants.NO_PROJECT_FOUND_ERROR.format(projectId=project_id))
        return self._to_project(proj)

    def get_project_assignment(self, project_id: str, user_id: str) -> project_assignment.ProjectAssignment | None:
        path_segments = [
            "internal",
            "projects",
            quote(project_id, safe=""),
            "users",
            quote(user_id, safe=""),
        ]

        response = self._aws_api.call_api(
            path="/".join(path_segments),
            http_method="GET",
        )

        return (
            project_assignment.ProjectAssignment.model_validate(response.get("assignment"))
            if response and response.get("assignment", None)
            else None
        )

    @staticmethod
    def _to_project(proj: dict) -> project.Project:
        return project.Project(
            projectId=proj["projectId"],
            projectName=proj["projectName"],
            projectDescription=proj["projectDescription"],
            workbenchLifecycle=proj.get("workbenchLifecycle"),
        )

    def _get_accounts(
        self,
        project_id: typing.Optional[str] = None,
        account_type: typing.Optional[str] = None,
        account_stage: typing.Optional[str] = None,
    ) -> list[dict]:
        params = {"pageSize": PAGE_SIZE}

        if project_id:
            params["projectId"] = project_id
        if account_stage:
            params["stage"] = account_stage
        if account_type:
            params["accountType"] = account_type

        accounts: list[dict] = []
        for page in paging.iterate_pages(
            lambda query: self._aws_api.call_api(path="internal/accounts", http_method="GET", query_params=query),
            params,
        ):
            if "projectAccounts" not in page:
                raise adapter_exception.AdapterException(constants.NO_ACCOUNT_ASSIGNED)
            accounts.extend(page["projectAccounts"] or [])
        return accounts

    def _iterate_projects(self) -> typing.Iterator[dict]:
        for page in paging.iterate_pages(
            lambda query: self._aws_api.call_api(path="internal/projects", http_method="GET", query_params=query),
            {"pageSize": PAGE_SIZE},
        ):
            if "projects" not in page:
                raise adapter_exception.AdapterException("The projects list answered no projects")
            yield from page["projects"] or []
