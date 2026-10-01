# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `several-stages-per-account` env config (default `false`): one AWS account may serve several stages (dev, qa, prod) of one project, one account record per type, stage, technology and region (portal and S2S onboarding); each stage then gets its own Service Catalog product (`<product>-<account>-<stage>`).
- `image-distribution` env config (`mode`: `share` default, or `store-restore`): with `store-restore`, publishing moves a version's image into each account (EC2 store image task into the account's import bucket, restore image task there) instead of sharing the image and its KMS key; the account launches its own copy, encrypted with its default EBS key. The product publishing enablement stack creates the import bucket and the `ProductPublishingImageImportRole` only in that mode. `storeWithFunctionRole` lets the ami-sharing function store the image itself where the image service account is the web application account.
- `ownerEmail` on provisioned products (record, provisioning API, workbench administration pages): the portal's sign-in e-mail, or for S2S and internal launches the project assignment's `userEmail`. The pages show it instead of the opaque user id when it is known.
- Added mandatory components lists to the Packaging S2S API: `GET /mandatory-components-lists` and `GET|PUT|DELETE /mandatory-components-lists/{platform}/{osVersion}/{architecture}` with `mandatory_components_list.read|write` scopes. `PUT` upserts a list from component and version ids (names are resolved), `DELETE` is idempotent. A change needs the client's assignment to the request's project and, when the deployment configures a releasing project for base images, must come from that project.
- A `SUPPORT` project role and a per-project `remoteSupportEnabled` setting (projects API and S2S create/update; omitted keeps the value, new projects default to enabled). While a project allows it, support staff may see the project and list its workbenches; they get no user rights of their own, and `ADMIN` includes `SUPPORT`. Only admins grant `SUPPORT`. The Authorization BC keeps the setting from `ProjectUpdated` and passes it to Cedar as `remoteSupportEnabled`.
- Platform-admin groups: members of the Entra groups in the `platform-admin-groups` config are `ADMIN` on every project without a grant per project (authorizer, project list, server-side role checks). The group list is returned with a project's groups.
- `self-enrolment-enabled` (default `true`): when `false`, users see only the projects they hold a role on; platform admins see all.
- Added a project-scoped OAuth S2S API for declarative component and component-version publishing.
- Added external-ID mappings, optimistic revisions, idempotent reconciliation operations, and lifecycle event recovery.
- Added service-client project assignments and dedicated Packaging read, write, release, and operation scopes.
- Added a project-scoped OAuth S2S Publishing API for products (create, update, archive) and product version promotion to a stage, with `clients/publishing` product and version scopes.
- Added an optional `onboardingRevision` on project accounts (Projects S2S `POST`/`PUT`): a new value re-runs onboarding of the unchanged configuration; the first value on an account without one is only recorded.
- Added `onboardedAt` on project accounts: set when onboarding first succeeds and never cleared, with a migration that backfills it for accounts onboarded earlier.

### Changed

- Publishing keys portfolios (`AWS_ACCOUNT#<account>#STAGE#<stage>`) and product versions (`VERSION#<version>#AWS_ACCOUNT#<account>#STAGE#<stage>`) per account and stage, and carries the stage through version events, the AMI-sharing state machine, retries and the internal version API; provisioning's version read model follows. Migrations 001/002 (publishing) and 002 (provisioning) move existing records on first start.
- Portal project updates publish the project's full state in `ProjectUpdated`, so they no longer reset the management mode the Authorization BC keeps.
- The workbench status sync runs every 5 minutes (`sync-job-cron-minute`, default `2/5`) instead of hourly, and the portal polls every 5 seconds while a workbench changes.
- Server-side role checks (the internal user-assignment route that launch reads, and `GET /projects/{projectId}/users/{userId}`) include the roles Entra group grants give, read from the user's Cognito record; a user reached only through a group could not launch.
- The authorizer passes the sign-in's trusted Entra groups on as `userGroups`, so the Projects API needs no second UserInfo call.
- Project member lists fill a missing email or display name from the identity provider on read.
- Component versions can be retired through the declarative API while released content remains immutable.

### Fixed

- Product templates are validated rendered, not as raw Jinja; CloudFormation rejected every version of the default templates ("YAML not well-formed"). Validation renders with a placeholder image and the image's architecture.
- A product's first automated version (a pipeline build for a product without versions) is created as `1.0.0-rc.1` instead of failing with "No released version found".
- The default templates list every region the image is available in, instead of `us-east-1` only.
- Workbenches join the spoke's workbench security group (`/proserve/wb/provisioning-enablement/pp-sg`), so a connection gateway admitted to it can reach them.
- Removing a workbench no longer fails when a policy was attached to its instance role from outside the template (for example by an SSM Quick Setup patch policy): an `InstanceRoleCleanup` custom resource, backed by a function in the spoke's provisioning enablement stack, removes such policies before the role is deleted.
- Project account create, update, re-onboard and S2S deactivate took their repository before entering the unit of work, which has repositories only inside its context, so every live call failed; the test doubles now enforce the same.
- A product template gets only the image in its own distribution's account (another account's copy in the same region could win), and each distribution's rendered template is stored under its own account and stage, since distributions of one version publish concurrently.
- The status sync closes a removal whose Service Catalog product is already gone after 5 minutes (a launch that failed before its stack existed never sends a stack event), and a settled workbench whose product was removed outside VEW after 10 minutes instead of 30.
- Stage access is enforced by the backend: a role may list and launch only the stages it may consume (`PLATFORM_USER` PROD, `BETA_USER` QA and PROD, contributors and above all stages). It used to shape only the product listing, so a direct API call could reach DEV or QA releases.
- Launching refuses a version whose project account is not `Active`.
- Product version listings return only versions of the project in the path.
- API Gateway's request validator now accepts `null` for fields the OpenAPI schemas declare `nullable`; it validates against JSON Schema draft 4, which ignores `nullable`, and answered `400` before the request reached the handler.

## [0.0.0] - 2022-08-17

### Added

- Pre-release placeholder
