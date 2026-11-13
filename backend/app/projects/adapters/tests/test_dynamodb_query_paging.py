"""the projects query service reads past one page and past BatchGetItem's 100 keys."""

from unittest import mock

import assertpy

from app.projects.adapters.query_services import dynamodb_query_service
from app.projects.adapters.repository import dynamo_entity_config
from app.projects.domain.model import enrolment, project, project_assignment

PROGRAMS = 300


def _projects_service(mock_dynamodb, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities, client=None):
    return dynamodb_query_service.DynamoDBProjectsQueryService(
        table_name=test_table_name,
        dynamodb_client=client or mock_dynamodb.meta.client,
        gsi_inverted_primary_key=gsi_name,
        gsi_aws_accounts=gsi_aws_accounts,
        gsi_entities=gsi_entities,
    )


def _fill(table, user_id: str, count: int):
    with table.batch_writer() as batch:
        for i in range(count):
            proj = project.Project(projectId=f"proj-{i:04d}", projectName=f"P{i}", isActive=True)
            batch.put_item(Item={"PK": f"PROJECT#{proj.projectId}", "SK": f"PROJECT#{proj.projectId}", "entity": "PROJECT", **proj.model_dump()})
            assignment = project_assignment.Assignment(userId=user_id, projectId=proj.projectId, roles=["PLATFORM_USER"])
            batch.put_item(Item={"PK": f"USER#{user_id}", "SK": f"PROJECT#{proj.projectId}", **assignment.model_dump()})


def test_list_projects_by_user_in_300_programs_on_one_page(
    mock_dynamodb, backend_app_dynamodb_table, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities
):
    # A page larger than 100 assignments used to fail BatchGetItem (100 keys at most).
    _fill(backend_app_dynamodb_table, "U1", PROGRAMS)
    srv = _projects_service(mock_dynamodb, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities)

    projects, token, assignments = srv.list_projects_by_user(user_id="U1", page_size=PROGRAMS, next_token=None)

    assertpy.assert_that({p.projectId for p in projects}).is_length(PROGRAMS)
    assertpy.assert_that(assignments).is_length(PROGRAMS)


def test_list_projects_by_user_pages_through_300_programs(
    mock_dynamodb, backend_app_dynamodb_table, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities
):
    _fill(backend_app_dynamodb_table, "U1", PROGRAMS)
    srv = _projects_service(mock_dynamodb, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities)

    seen, token, pages = set(), None, 0
    while True:
        projects, token, _ = srv.list_projects_by_user(user_id="U1", page_size=100, next_token=token)
        seen |= {p.projectId for p in projects}
        pages += 1
        if not token:
            break

    assertpy.assert_that(seen).is_length(PROGRAMS)
    assertpy.assert_that(pages).is_between(3, 4)


def test_batch_get_retries_unprocessed_keys(test_table_name, gsi_name, gsi_aws_accounts, gsi_entities):
    item = {"PK": "PROJECT#p1", "SK": "PROJECT#p1", "projectId": "p1", "projectName": "P1", "isActive": True}
    client = mock.MagicMock()
    client.query.return_value = {"Items": [{"PK": "USER#U1", "SK": "PROJECT#p1", "userId": "U1", "projectId": "p1", "roles": []}]}
    client.batch_get_item.side_effect = [
        {"Responses": {test_table_name: []}, "UnprocessedKeys": {test_table_name: {"Keys": [{"PK": "PROJECT#p1", "SK": "PROJECT#p1"}]}}},
        {"Responses": {test_table_name: [item]}, "UnprocessedKeys": {}},
    ]
    srv = _projects_service(None, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities, client=client)

    with mock.patch.object(dynamodb_query_service.time, "sleep"):
        projects, _, _ = srv.list_projects_by_user(user_id="U1", page_size=10, next_token=None)

    assertpy.assert_that([p.projectId for p in projects]).is_equal_to(["p1"])
    assertpy.assert_that(client.batch_get_item.call_count).is_equal_to(2)


def test_list_project_accounts_by_aws_account_reads_every_page(test_table_name, gsi_name, gsi_aws_accounts, gsi_entities):
    def account(i):
        return {
            "PK": "PROJECT#p1", "SK": f"ACCOUNT#a{i}", "id": f"a{i}", "projectId": "p1", "awsAccountId": "123456789012",
            "accountType": "USER", "accountName": "n", "accountDescription": "d", "createDate": "2026-01-01",
            "lastUpdateDate": "2026-01-01", "accountStatus": "Active", "stage": "dev", "technologyId": "t",
            "region": "eu-north-1",
        }

    client = mock.MagicMock()
    client.query.side_effect = [
        {"Items": [account(1)], "LastEvaluatedKey": {"PK": "PROJECT#p1", "SK": "ACCOUNT#a1"}},
        {"Items": [account(2)]},
    ]
    srv = _projects_service(None, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities, client=client)

    accounts = srv.list_project_accounts_by_aws_account("123456789012")

    assertpy.assert_that([a.id for a in accounts]).is_equal_to(["a1", "a2"])
    assertpy.assert_that(client.query.call_args_list[1].kwargs["ExclusiveStartKey"]).is_equal_to(
        {"PK": "PROJECT#p1", "SK": "ACCOUNT#a1"}
    )


def test_get_enrolment_for_user_finds_one_past_the_first(
    mock_dynamodb, backend_app_dynamodb_table, test_table_name, gsi_qpk, gsi_qsk
):
    # Limit=1 with a filter read only the program's first enrolment.
    with backend_app_dynamodb_table.batch_writer() as batch:
        for i in range(PROGRAMS):
            e = enrolment.Enrolment(projectId="p1", userId=f"U{i:04d}", status=enrolment.EnrolmentStatus.Pending)
            batch.put_item(
                Item={
                    "PK": f"{dynamo_entity_config.DBPrefix.PROJECT}#p1",
                    "SK": f"{dynamo_entity_config.DBPrefix.ENROLMENT}#{e.id}",
                    **e.model_dump(),
                }
            )
    srv = dynamodb_query_service.DynamoDBEnrolmentQueryService(
        table_name=test_table_name, dynamodb_client=mock_dynamodb.meta.client, gsi_qpk=gsi_qpk, gsi_qsk=gsi_qsk
    )

    found = [srv.get_enrolment_for_user(user_id=f"U{i:04d}", project_id="p1") for i in (0, 150, 299)]

    assertpy.assert_that([e.userId for e in found]).is_equal_to(["U0000", "U0150", "U0299"])
    assertpy.assert_that(srv.get_enrolment_for_user(user_id="NOBODY", project_id="p1")).is_none()
