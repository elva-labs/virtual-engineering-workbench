import enum
import typing
from abc import ABC, abstractmethod

import jwt
import pydantic
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from pydantic import ConfigDict

from app.authorization.domain.ports import assignments_query_service, authentication_service, authorization_service
from app.authorization.domain.read_models import project_assignment
from app.authorization.domain.read_models import project_assignment as project_assignment_model
from app.authorization.domain.services.auth import authorizer
from app.shared.identity.entra_groups import effective_roles, group_ids

USER_ID_CLAIM_NAME = "custom:user_tid"


class JWTAuthorizer(authorizer.AuthorizerStep):

    def __init__(
        self,
        auth_srv: authentication_service.AuthenticationService,
        logger: Logger,
        metrics: Metrics,
        issuer: str,
        audiences: list[str] = [],
    ):
        self.__auth_srv = auth_srv
        self.__logger = logger
        self.__metrics = metrics
        self.__issuer = issuer
        self.__audiences = audiences

    def invoke(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
    ) -> bool:

        auth_token_jwt, signing_key = self.__get_auth_token_with_signing_key(request)

        if not auth_token_jwt or not signing_key:
            return False

        try:
            token = jwt.decode(
                auth_token_jwt,
                signing_key.key,
                algorithms=["RS256"],
                options={
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": False,
                    "require": ["exp", "iss", "token_use"],
                },
                issuer=self.__issuer,
            )

            self.__logger.debug(
                {
                    "message": "JWT token decoded successfully",
                    "jwt_kid": signing_key.key_id,
                }
            )

            self.__validate_audience(token)
            self.__validate_token_use(token)

            return True

        except jwt.exceptions.InvalidSignatureError:
            self.__logger.exception("The JWT token has an invalid signature")
            self.__metrics.add_metric(name="CognitoInvalidToken", unit=MetricUnit.Count, value=1)
        except jwt.exceptions.ExpiredSignatureError:
            self.__logger.exception("The JWT token has expired")
            self.__metrics.add_metric(name="CognitoInvalidToken", unit=MetricUnit.Count, value=1)
        except jwt.exceptions.DecodeError:
            self.__logger.exception("The JWT token could not be decoded")
            self.__metrics.add_metric(name="TokenDecodeError", unit=MetricUnit.Count, value=1)
        except Exception:
            self.__logger.exception("Unexpected error while validating JWT")
            self.__metrics.add_metric(name="UnexpectedUserProfileError", unit=MetricUnit.Count, value=1)

        return False

    def __validate_audience(self, token: dict):
        if self.__audiences:
            token_client_id = token.get("aud") or token.get("client_id")
            if token_client_id not in self.__audiences:
                self.__logger.error("The JWT token has an invalid audience")
                raise jwt.exceptions.InvalidAudienceError("Invalid client ID")

    def __validate_token_use(self, token: dict):
        if token.get("token_use") not in ["id", "access"]:
            self.__logger.error("JWT is neither ID nor Access token.")
            raise jwt.exceptions.InvalidTokenError("JWT is neither ID nor Access token.")

    def __get_auth_token_with_signing_key(
        self, request: authorizer.AuthorizationRequest
    ) -> tuple[str | None, jwt.PyJWK | None]:
        if not request.auth_token.startswith("Bearer "):
            return (None, None)

        auth_token_jwt = request.auth_token.split("Bearer ")[1]
        success, signing_key = self.__auth_srv.get_signing_key_from_jwt(auth_token_jwt)
        if not success or not signing_key:
            return (None, None)

        return (auth_token_jwt, signing_key)


class CognitoAuthorizer(authorizer.AuthorizerStep):

    def __init__(
        self,
        auth_srv: authentication_service.AuthenticationService,
        logger: Logger,
        metrics: Metrics,
    ):
        self.__auth_srv = auth_srv
        self.__logger = logger
        self.__metrics = metrics

    def invoke(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
    ) -> bool:

        if not request.auth_token.startswith("Bearer "):
            return False

        if user_profile := self.__auth_srv.get_user_info(request.auth_token):
            self.__logger.debug(
                {
                    "message": "Trusted identity profile retrieved",
                }
            )
            context.user_name = (
                self.__sanitize_user_name(user_profile[USER_ID_CLAIM_NAME])
                if USER_ID_CLAIM_NAME in user_profile
                else None
            )
            context.trusted_group_ids = group_ids(user_profile)
            context.user_email = user_profile["email"]
            return True

        self.__logger.debug(
            {
                "message": "User Profile could not be retrieved",
            }
        )
        self.__metrics.add_metric(name="UserInvalid", unit=MetricUnit.Count, value=1)
        return False

    def __sanitize_user_name(self, user_name: str) -> str:
        username = user_name.split("@")[0] if "@" in user_name else user_name
        return username.upper()


class ProjectsBCContextEnricher(authorizer.AuthorizerStep):

    def __init__(
        self,
        assignments_query_service: assignments_query_service.AssignmentsQueryService,
        platform_admin_groups: typing.Iterable[str] = (),
    ):
        self.__assignments_query_service = assignments_query_service
        # Members of these Entra groups are ADMIN on every project (config "platform-admin-groups").
        self.__platform_admin_groups = {g.lower() for g in platform_admin_groups}

    def invoke(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
    ) -> bool:

        if not context.user_name:
            return False

        if (
            context.api_auth_cfg.bounded_context not in context.project_scoped_bounded_contexts
            and authorizer.AuthFeature.ProjectAssignments not in context.api_auth_cfg.auth_features
        ):
            return True

        context.roles = []
        context.domains = []

        selected_assignment = None

        direct = self.__assignments_query_service.get_user_assignments(user_id=context.user_name)
        groups = (
            self.__assignments_query_service.get_group_assignments(context.trusted_group_ids)
            if context.trusted_group_ids
            else []
        )
        union = effective_roles(direct, groups)
        by_project = {assignment.projectId: assignment for assignment in direct}
        # These assignments exist only in the authorization request; no user records are written.
        context.project_assignments = [
            (
                by_project[project_id].model_copy(update={"roles": roles})
                if project_id in by_project
                else project_assignment.Assignment(userId=context.user_name, projectId=project_id, roles=roles)
            )
            for project_id, roles in union.items()
        ]

        if request.resource_path.startswith("/projects/") and (
            project_id := request.resource_ids.get("projectId", None)
        ):
            selected_assignment = next(
                (assignment for assignment in context.project_assignments if assignment.projectId == project_id),
                None,
            )
            if self.__is_platform_admin(context):
                selected_assignment = self.__with_platform_admin(context, project_id, selected_assignment)

            settings = self.__assignments_query_service.get_project_settings(project_id=project_id)
            # Before the roles are set: a workbench-only program reduces them.
            if settings.experience == WORKBENCH_ONLY_EXPERIENCE:
                selected_assignment = self.__as_workbench_user(context, project_id, selected_assignment)
                is_admin = bool(
                    selected_assignment and project_assignment_model.Role.ADMIN in (selected_assignment.roles or [])
                )
                # No product management (packaging, publishing) in a workbench-only program,
                # for anyone but an ADMIN.
                if not is_admin and context.api_auth_cfg.bounded_context in WORKBENCH_ONLY_CLOSED_CONTEXTS:
                    return False
            context.roles = selected_assignment.roles if selected_assignment and selected_assignment.roles else []
            context.domains = (
                list({g.get("domain") for g in selected_assignment.activeDirectoryGroups if "domain" in g})
                if selected_assignment and selected_assignment.activeDirectoryGroups
                else []
            )
            context.remote_support_enabled = settings.remoteSupportEnabled
            context.project_managed_by = settings.managedBy
            context.project_managed_source = settings.managedSource
            context.project_experience = settings.experience
        elif self.__is_platform_admin(context):
            # No project in the path: count as an admin somewhere, which CreateProject requires.
            context.project_assignments = context.project_assignments + [
                self.__group_assignment(context, PLATFORM_ADMIN_PROJECT_ID, [project_assignment_model.Role.ADMIN])
            ]

        return True

    def __is_platform_admin(self, context: authorizer.AuthorizationContext) -> bool:
        return bool({g.lower() for g in context.trusted_group_ids} & self.__platform_admin_groups)

    def __with_platform_admin(
        self,
        context: authorizer.AuthorizationContext,
        project_id: str,
        assignment: project_assignment_model.Assignment | None,
    ) -> project_assignment_model.Assignment:
        """Adds ADMIN for members of a platform-admin group, on top of the effective roles (direct and
        Entra group grants), without a grant per project. The merged assignment replaces the one in
        context.project_assignments, so the Cedar entities need no change."""
        admin = project_assignment_model.Role.ADMIN
        if assignment and admin in assignment.roles:
            return assignment
        own_admin_roles = {admin, project_assignment_model.Role.PROGRAM_OWNER}
        context.platform_admin_access = not (assignment and own_admin_roles & set(assignment.roles or []))
        merged = (
            assignment.model_copy(update={"roles": [*assignment.roles, admin]})
            if assignment
            else self.__group_assignment(context, project_id, [admin])
        )
        context.project_assignments = [a for a in context.project_assignments if a.projectId != project_id] + [merged]
        return merged

    def __as_workbench_user(
        self,
        context: authorizer.AuthorizationContext,
        project_id: str,
        assignment: project_assignment_model.Assignment | None,
    ) -> project_assignment_model.Assignment | None:
        """In a workbench-only program the members keep only the roles that program has use for.

        PLATFORM_USER (their own workbenches), PROGRAM_OWNER (the program's members, their access and
        workbenches) and SUPPORT (remote support) stay; product contributors, power and beta users act as
        users there. An ADMIN keeps everything. The reduced assignment replaces the one in
        context.project_assignments, so the Cedar principal carries the reduced roles too. Stages and
        product types are limited by the provisioning API (projectExperience in the authorizer context).
        """
        roles = project_assignment_model.Role
        if not assignment or not assignment.roles or roles.ADMIN in assignment.roles:
            return assignment
        kept = [roles.PLATFORM_USER] + [
            role for role in (roles.PROGRAM_OWNER, roles.SUPPORT) if role in assignment.roles
        ]
        if sorted(assignment.roles) == sorted(kept):
            return assignment
        reduced = assignment.model_copy(update={"roles": kept})
        context.project_assignments = [a for a in context.project_assignments if a.projectId != project_id] + [reduced]
        return reduced

    def __group_assignment(
        self, context: authorizer.AuthorizationContext, project_id: str, roles: list
    ) -> project_assignment_model.Assignment:
        return project_assignment_model.Assignment(
            userId=context.user_name, projectId=project_id, roles=roles, userEmail=context.user_email
        )


# The program experience that limits members to their workbenches (projects BC model).
WORKBENCH_ONLY_EXPERIENCE = "workbench-only"
# Product management (images, products) is closed in such a program for everyone but an ADMIN.
WORKBENCH_ONLY_CLOSED_CONTEXTS = {"packaging", "publishing"}


# Synthetic project id of a platform admin's assignment when no project is in the path; it matches
# no real project (ids are proj-xxxxx), so it only feeds totalAdminAssignments.
PLATFORM_ADMIN_PROJECT_ID = "platform-admin"


# Project entity attributes for remote support; names match the Cedar schema (infra/auth/shared_auth_schema.py).
SUPPORTERS_ATTRIBUTE = "supporters"
REMOTE_SUPPORT_ENABLED_ATTRIBUTE = "remoteSupportEnabled"


class AVPEntityType(enum.StrEnum):
    PROJECT = "VEW::Project"
    PROJECT_ASSIGNMENT = "VEW::ProjectAssignment"
    USER = "VEW::User"


class AVPEntityIdentifier(pydantic.BaseModel):
    entity_id: str = pydantic.Field(alias="entityId")
    entity_type: str = pydantic.Field(alias="entityType")
    model_config = ConfigDict(populate_by_name=True)


class AVPEntity(pydantic.BaseModel):
    identifier: AVPEntityIdentifier = pydantic.Field(..., alias="identifier")
    attributes: dict = pydantic.Field({}, alias="attributes")
    parents: list[AVPEntityIdentifier] = pydantic.Field([], alias="parents")


class AVPEntityResolutionContext(pydantic.BaseModel):
    resource: AVPEntityIdentifier | None = pydantic.Field(None)
    entities: list[AVPEntity] = pydantic.Field([], alias="entities")


class AVPEntityResolver(ABC):

    @abstractmethod
    def resolve(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
        avp_entities: AVPEntityResolutionContext,
    ): ...


class VEWProjectAssignmentEntityResolver(AVPEntityResolver):
    def resolve(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
        avp_entities: AVPEntityResolutionContext,
    ):
        if (
            context.api_auth_cfg.bounded_context not in context.project_scoped_bounded_contexts
            and authorizer.AuthFeature.ProjectAssignments not in context.api_auth_cfg.auth_features
        ):
            return

        if request.resource_path.startswith("/projects/") and (
            project_id := request.resource_ids.get("projectId", None)
        ):
            avp_entities.resource = AVPEntityIdentifier(
                entity_id=project_id,
                entity_type=AVPEntityType.PROJECT,
            )
            avp_entities.entities.append(
                self.__generate_project_entity(
                    project_id=project_id, remote_support_enabled=context.remote_support_enabled
                )
            )
            avp_entities.entities.extend(self.__generate_project_assignment_inheritace_scheme(project_id=project_id))
            avp_entities.entities.append(self.__generate_support_assignment_entity(project_id=project_id))
            avp_entities.entities.extend(
                self.__generate_project_assignment_group_based_inheritace_scheme(project_id=project_id)
            )

        if not context.user_name:
            return

        user_entity = next(
            (
                e
                for e in avp_entities.entities
                if e.identifier.entity_type == AVPEntityType.USER and e.identifier.entity_id == context.user_name
            ),
            AVPEntity(
                identifier=AVPEntityIdentifier(
                    entityId=context.user_name,
                    entityType=AVPEntityType.USER,
                ),
            ),
        )

        user_parents = [
            AVPEntityIdentifier(
                entityId=self.__generate_assignment_id(assignment.projectId, role),
                entityType=AVPEntityType.PROJECT_ASSIGNMENT,
            )
            for assignment in context.project_assignments
            for role in assignment.roles
        ]

        user_parents_group = [
            AVPEntityIdentifier(
                entityId=self.__generate_group_assignment_id(assignment.projectId, group),
                entityType=AVPEntityType.PROJECT_ASSIGNMENT,
            )
            for assignment in context.project_assignments
            for group in assignment.groupMemberships
        ]

        total_admin_assignments = len(
            [a for a in (context.project_assignments or []) if project_assignment.Role.ADMIN in a.roles]
        )

        if not user_entity.parents:
            user_entity.parents = user_parents + user_parents_group
        else:
            user_entity.parents.extend(user_parents)
            user_entity.parents.extend(user_parents_group)

        user_entity.attributes["totalAdminAssignments"] = {"long": total_admin_assignments}

    def __generate_project_entity(self, project_id: str, remote_support_enabled: bool = True) -> dict:

        role_based_attributes = {
            groupName: {"entityIdentifier": identifier}
            for groupName, identifier in self.__generate_assignment_entity_set(project_id=project_id)
        }

        group_based_attributed = {
            groupName: {"entityIdentifier": identifier}
            for groupName, identifier in self.__generate_group_based_assignment_entity_set(project_id=project_id)
        }

        support_attributes = {
            SUPPORTERS_ATTRIBUTE: {"entityIdentifier": self.__generate_support_assignment_id(project_id)},
            REMOTE_SUPPORT_ENABLED_ATTRIBUTE: {"boolean": remote_support_enabled},
        }

        combined_attributes = {**role_based_attributes, **group_based_attributed, **support_attributes}

        return AVPEntity(
            identifier=AVPEntityIdentifier(
                entityId=project_id,
                entityType=AVPEntityType.PROJECT,
            ),
            attributes=combined_attributes,
        )

    def __generate_project_assignment_inheritace_scheme(self, project_id: str):
        assignmentIds = self.__generate_assignment_entity_set(project_id=project_id)

        entities = [
            AVPEntity(
                identifier=assignmentId,
                parents=([assignmentIds[idx + 1][1]] if idx + 1 < len(assignmentIds) else []),
            )
            for idx, (_, assignmentId) in enumerate(assignmentIds)
        ]
        # ADMIN includes SUPPORT; SUPPORT itself is outside the user chain (no user rights of its own).
        entities[0].parents.append(self.__generate_support_assignment_id(project_id))
        return entities

    def __generate_support_assignment_id(self, project_id: str) -> AVPEntityIdentifier:
        return AVPEntityIdentifier(
            entityType=AVPEntityType.PROJECT_ASSIGNMENT,
            entityId=self.__generate_assignment_id(project_id, project_assignment.Role.SUPPORT.value),
        )

    def __generate_support_assignment_entity(self, project_id: str) -> AVPEntity:
        return AVPEntity(identifier=self.__generate_support_assignment_id(project_id), parents=[])

    def __generate_project_assignment_group_based_inheritace_scheme(self, project_id: str):
        assignmentIds = self.__generate_group_based_assignment_entity_set(project_id=project_id)

        return [AVPEntity(identifier=assignmentId, parents=[]) for _, (_, assignmentId) in enumerate(assignmentIds)]

    def __generate_assignment_id(self, project_id: str, role: str):
        return f"{project_id}#{role}"

    def __generate_assignment_entity_set(self, project_id: str) -> typing.Iterable[AVPEntityIdentifier]:

        return [
            (
                projectGroupName,
                AVPEntityIdentifier(
                    entityType=AVPEntityType.PROJECT_ASSIGNMENT,
                    entityId=self.__generate_assignment_id(project_id, role),
                ),
            )
            for projectGroupName, role in [
                ("admins", "ADMIN"),
                ("programOwners", "PROGRAM_OWNER"),
                ("powerUsers", "POWER_USER"),
                ("productContributors", "PRODUCT_CONTRIBUTOR"),
                ("betaUsers", "BETA_USER"),
                ("platformUsers", "PLATFORM_USER"),
            ]
        ]

    def __generate_group_assignment_id(self, project_id: str, group: str):
        return f"{project_id}#GROUP#{group}"

    def __generate_group_based_assignment_entity_set(self, project_id: str) -> typing.Iterable[AVPEntityIdentifier]:

        return [
            (
                projectGroupName,
                AVPEntityIdentifier(
                    entityType=AVPEntityType.PROJECT_ASSIGNMENT,
                    entityId=self.__generate_group_assignment_id(project_id, group),
                ),
            )
            for projectGroupName, group in [
                ("vewUsers", "VEW_USERS"),
                ("hilUsers", "HIL_USERS"),
                ("vvplUsers", "VVPL_USERS"),
            ]
        ]


class AmazonVerifiedPermissionsAuthorizer(authorizer.AuthorizerStep):

    def __init__(
        self,
        authz_service: authorization_service.AuthorizationService,
        logger: Logger,
        entity_resolvers: list[AVPEntityResolver] = [],
    ):
        self.__authz_service = authz_service
        self.__logger = logger
        self.__entity_resolvers = entity_resolvers

    def invoke(
        self,
        request: authorizer.AuthorizationRequest,
        context: authorizer.AuthorizationContext,
    ) -> bool:
        if not context.user_name:
            return False

        if context.api_auth_cfg.policy_store_id is None:
            return False

        principal = AVPEntityIdentifier(entityType=AVPEntityType.USER, entityId=context.user_name)

        action = {
            "actionId": request.operation_id,
            "actionType": "VEW::Action",
        }

        entity_ctx = AVPEntityResolutionContext(
            entities=[AVPEntity(identifier=principal.model_copy())],
        )

        for entity_resolver in self.__entity_resolvers:
            entity_resolver.resolve(request=request, context=context, avp_entities=entity_ctx)

        try:
            if self.__authz_service.is_action_allowed(
                policy_store_id=context.api_auth_cfg.policy_store_id,
                principal=principal.model_dump(exclude_none=True, by_alias=True),
                action=action,
                resource=(
                    entity_ctx.resource.model_dump(exclude_none=True, by_alias=True) if entity_ctx.resource else None
                ),
                entities={"entityList": [e.model_dump(exclude_none=True, by_alias=True) for e in entity_ctx.entities]},
            ):
                return True

        except Exception:
            self.__logger.exception("Error invoking Amazon Verified Permissions")

        return False
