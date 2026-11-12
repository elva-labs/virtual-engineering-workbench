"""A fake of the projects internal API that pages like the real one.

Each list route answers at most `pageSize` items and a `nextToken` object (as DynamoDB's
LastEvaluatedKey), and expects that object back as JSON in the `nextToken` query parameter, as the
projects handler decodes it. A client that reads only page 1, or sends the token in another form,
fails these tests.
"""

import json


class PagedProjectsApi:
    def __init__(self, projects: int = 0, accounts_per_project: int = 0, user_assignments: int = 0):
        self.projects = [
            {"projectId": f"proj-{i:04d}", "projectName": f"Program {i}", "projectDescription": f"Description {i}"}
            for i in range(projects)
        ]
        self.accounts = {
            project["projectId"]: [
                {
                    "id": f"{project['projectId']}-acct-{j:04d}",
                    "awsAccountId": f"{i:06d}{j:06d}",
                    "stage": "dev",
                    "region": "eu-north-1",
                    "accountStatus": "Active" if j % 2 == 0 else "Inactive",
                }
                for j in range(accounts_per_project)
            ]
            for i, project in enumerate(self.projects)
        }
        self.user_assignments = [{"projectId": f"proj-{i:04d}", "roles": ["PLATFORM_USER"]} for i in range(user_assignments)]
        self.calls: list[tuple[str, dict]] = []

    def call_api(self, path: str, http_method: str, query_params: dict | None = None, **_) -> dict:
        params = dict(query_params or {})
        self.calls.append((path, params))
        if path == "internal/projects":
            items, token = self._page(self.projects, params)
            return {"projects": items, "nextToken": token}
        if path == "internal/accounts":
            items, token = self._page(self.accounts.get(params.get("projectId"), []), params)
            return {"projectAccounts": items, "nextToken": token}
        if path == "internal/user/assignments":
            items, token = self._page(self.user_assignments, params)
            return {"projects": [], "assignments": items, "nextToken": token}
        raise AssertionError(f"Unexpected path {path}")

    def calls_to(self, path: str) -> list[dict]:
        return [params for called, params in self.calls if called == path]

    @staticmethod
    def _page(items: list, params: dict) -> tuple[list, dict | None]:
        size = int(params["pageSize"])
        raw = params.get("nextToken")
        if raw is not None and not isinstance(raw, str):
            raise AssertionError(f"nextToken must be sent as JSON, got {type(raw).__name__}")
        start = int(json.loads(raw)["SK"].split("#")[1]) if raw else 0
        end = start + size
        token = {"PK": "PAGE", "SK": f"OFFSET#{end}"} if end < len(items) else None
        return items[start:end], token
