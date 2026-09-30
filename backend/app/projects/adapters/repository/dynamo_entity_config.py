import enum

from app.projects.domain.model import (
    enrolment,
    project,
    project_account,
    project_create_request,
    project_assignment,
    project_group_assignment,
    service_client_assignment,
    technology,
    user,
)
from app.shared.adapters.unit_of_work_v2 import dynamodb_repo_config, dynamodb_repository


class DBPrefix(enum.StrEnum):
    PROJECT = "PROJECT"
    ACCOUNT = "ACCOUNT"
    USER = "USER"
    TECHNOLOGY = "TECHNOLOGY"
    ENROLMENT = "ENROLMENT"
    CLIENT = "CLIENT"
    GROUP = "GROUP"
    IDEMPOTENCY = "IDEMPOTENCY"


class EntityConfigurator(dynamodb_repository.DynamoDBEntityConfiguratorBase):
    def __init__(self, table_name: str) -> None:
        """DynamoDB Entity configuration for Projects BC.

        Projects BC DynamoDB table has the following Global Secondary Indexes:
        | Name                                   | Partition Key attr. | Sort Key attr. |
        |  -                                     | PK                  | SK             |
        | gsi_inverted_primary_key               | SK                  | PK             |
        | gsi_aws_accounts                       | awsAccountId        |                |
        | gsi_entities                           | entity              | PK             |
        | gsi_query_pk                           | QPK                 | SK             |
        | gsi_query_sk                           | PK                  | QSK

        These indexes can be reused across different entities.
        """

        super().__init__(table_name)
        self.register_cfg(
            project_account.ProjectAccountPrimaryKey,
            project_account.ProjectAccount,
            self.project_account_entity_config,
        )
        self.register_cfg(
            project_create_request.ProjectCreateRequestPrimaryKey,
            project_create_request.ProjectCreateRequest,
            self.project_create_request_entity_config,
        )
        self.register_cfg(
            project.ProjectPrimaryKey,
            project.Project,
            self.project_entity_config,
        )
        self.register_cfg(
            project_assignment.AssignmentPrimaryKey,
            project_assignment.Assignment,
            self.project_assignment_entity_config,
        )
        self.register_cfg(
            project_group_assignment.ProjectGroupAssignmentPrimaryKey,
            project_group_assignment.ProjectGroupAssignment,
            self.project_group_assignment_entity_config,
        )
        self.register_cfg(
            technology.TechnologyPrimaryKey,
            technology.Technology,
            self.technology_entity_config,
        )
        self.register_cfg(
            enrolment.EnrolmentPrimaryKey,
            enrolment.Enrolment,
            self.enrolment_entity_config,
        )
        self.register_cfg(
            user.UserPrimaryKey,
            user.User,
            self.user_entity_config,
        )
        self.register_cfg(
            service_client_assignment.ServiceClientAssignmentPrimaryKey,
            service_client_assignment.ServiceClientAssignment,
            self.service_client_assignment_entity_config,
        )

    def project_create_request_entity_config(
        self, cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[project_create_request.ProjectCreateRequestPrimaryKey, project_create_request.ProjectCreateRequest]
    ):
        cfg.partition_key(
            name="PK", value_template=lambda client_id: f"{DBPrefix.IDEMPOTENCY}#CREATE_PROJECT#{client_id}",
            values_from_entity=lambda ent: ent.clientId, values_from_primary_key=lambda pk: pk.clientId,
        )
        cfg.sort_key(
            name="SK", value_template=lambda key: f"KEY#{key}",
            values_from_entity=lambda ent: ent.idempotencyKey, values_from_primary_key=lambda pk: pk.idempotencyKey,
        )

    def service_client_assignment_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[
            service_client_assignment.ServiceClientAssignmentPrimaryKey,
            service_client_assignment.ServiceClientAssignment,
        ],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda client_id: f"{DBPrefix.CLIENT}#{client_id}",
            values_from_entity=lambda ent: ent.clientId,
            values_from_primary_key=lambda pk: pk.clientId,
        )
        cfg.sort_key(
            name="SK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )
        cfg.enable_optimistic_concurrency_control()

    def project_account_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[
            project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
        ],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda account_id: f"{DBPrefix.ACCOUNT}#{account_id}",
            values_from_entity=lambda ent: ent.id,
            values_from_primary_key=lambda pk: pk.id,
        )

        cfg.enable_optimistic_concurrency_control()

    def project_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[project.ProjectPrimaryKey, project.Project],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )

        cfg.enable_optimistic_concurrency_control()

        cfg.enable_query_all(gsi_partition_key_attribute_name=DBPrefix.PROJECT)

    def project_assignment_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[
            project_assignment.AssignmentPrimaryKey, project_assignment.Assignment
        ],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda user_id: f"{DBPrefix.USER}#{user_id}",
            values_from_entity=lambda ent: ent.userId,
            values_from_primary_key=lambda pk: pk.userId,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )

    def project_group_assignment_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[
            project_group_assignment.ProjectGroupAssignmentPrimaryKey,
            project_group_assignment.ProjectGroupAssignment,
        ],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda group_id: f"{DBPrefix.GROUP}#{group_id}",
            values_from_entity=lambda ent: ent.groupId,
            values_from_primary_key=lambda pk: pk.groupId,
        )
        cfg.sort_key(
            name="SK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )
        cfg.enable_optimistic_concurrency_control()

    def technology_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[
            technology.TechnologyPrimaryKey, technology.Technology
        ],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda project_id: f"{DBPrefix.PROJECT}#{project_id}",
            values_from_entity=lambda ent: ent.project_id,
            values_from_primary_key=lambda pk: pk.project_id,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda id: f"{DBPrefix.TECHNOLOGY}#{id}",
            values_from_entity=lambda ent: ent.id,
            values_from_primary_key=lambda pk: pk.id,
        )

    def enrolment_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[enrolment.EnrolmentPrimaryKey, enrolment.Enrolment],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda projectId: f"{DBPrefix.PROJECT}#{projectId}",
            values_from_entity=lambda ent: ent.projectId,
            values_from_primary_key=lambda pk: pk.projectId,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda id: f"{DBPrefix.ENROLMENT}#{id}",
            values_from_entity=lambda ent: ent.id,
            values_from_primary_key=lambda pk: pk.id,
        )

        cfg.enable_query_pattern(
            gsi_pk_name="QPK",
            gsi_pk_value_template=lambda user_id: f"{DBPrefix.USER}#{user_id}",
            gsi_pk_values_from_entity=lambda ent: ent.userId,
            gsi_sk_name="QSK",
            gsi_sk_value_template=lambda status, id: f"{DBPrefix.ENROLMENT}#{status}#{id}",
            gsi_sk_values_from_entity=lambda ent: [ent.status, ent.id],
        )

    def user_entity_config(
        self,
        cfg: dynamodb_repo_config.GenericDynamoDBRepositoryConfig[user.UserPrimaryKey, user.User],
    ):
        cfg.partition_key(
            name="PK",
            value_template=lambda user_id: f"{DBPrefix.USER}#{user_id}",
            values_from_entity=lambda ent: ent.userId,
            values_from_primary_key=lambda pk: pk.userId,
        )

        cfg.sort_key(
            name="SK",
            value_template=lambda user_id: f"{DBPrefix.USER}#{user_id}",
            values_from_entity=lambda ent: ent.userId,
            values_from_primary_key=lambda pk: pk.userId,
        )

        cfg.enable_query_all(gsi_partition_key_attribute_name=DBPrefix.USER)
