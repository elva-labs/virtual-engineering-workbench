# Elva Labs additions to Virtual Engineering Workbench

This page describes the **current code differences** between Elva Labs `main` and
the official [awslabs VEW](https://github.com/awslabs/virtual-engineering-workbench).
It is not a list of fork commits. Features already implemented equivalently upstream
are not listed here. An API or configuration described below is a code contract, not
a claim that it has been deployed to an AWS environment.

## Staying close to upstream

Elva's `main` is the working branch. The `awslabs` branch mirrors official upstream
`main`: [Sync awslabs](../../.github/workflows/sync-awslabs.yml) is scheduled daily at
06:17 UTC and fast-forwards that branch, not Elva's `main`. After the sync, a
scheduled Stig worker rebases `main` onto `awslabs` daily at 07:00 UTC. It resolves
conflicts by behavior and takes upstream's version when upstream now covers a fork
feature. It runs backend and frontend tests plus CI on `rebase/candidate`. It saves
a `backup/main-<date>` branch and tag, then force-updates `main` with a lease. It
skips publishing while other work is open against `main` and asks a maintainer when
a conflict is unclear. Between runs, `main` can be up to a day behind upstream.
See the [manual rebase procedure](../../CONTRIBUTING.md#rebase-elvas-main-onto-awslabs)
for doing this by hand.

Configuration below lives in [`backend/infra/config.py`](../../backend/infra/config.py)
unless stated otherwise. Changing deployment configuration requires deploying the
affected stacks; a service-client scope also needs an OAuth client grant. In addition
to scopes, project-scoped S2S operations require a project/client assignment.

## Packaging automation beyond upstream

Upstream already has project-scoped S2S component, recipe, pipeline and image
endpoints. Elva extends their behavior; it does not introduce that basic API.

- **Recoverable, idempotent creates.** `POST` of components, component versions,
  recipes, recipe versions, pipelines and images under
  `/clients/packaging/v1/projects/{projectId}` requires an RFC 4122 UUID
  `Idempotency-Key`. The reservation stores a resource ID and response. A retry
  with the same request can recover an expired lease and resume creation or workflow
  publication without inventing a second resource. Async creates return `202` and
  `Retry-After: 5`; poll the corresponding GET for status. An image build keeps its
  client token across retries. Structured component definitions are validated in
  the handler; recipe versions expose both configured and effective components.
  Grant the existing `clients/packaging/{component,recipe,pipeline}.*` scopes for
  the operations used and assign the client to the project. Persistence uses
  [`s2s_api/idempotency.py`](../../backend/app/packaging/entrypoints/s2s_api/idempotency.py)
  and the [shared reservation store](../../backend/app/shared/adapters/idempotency/dynamodb_idempotency_service.py).
  See the [Packaging S2S API contract](../packaging-s2s-api.md) for all routes,
  scope names, status codes and retry rules.
- **Mandatory component lists over S2S.** Read the global
  `/clients/packaging/v1/mandatory-components-lists` collection or an individual
  `/{platform}/{osVersion}/{architecture}` with
  `clients/packaging/mandatory_components_list.read`. `PUT` upserts prepended and
  appended component versions; `DELETE` removes the list. Writes require
  `clients/packaging/mandatory_components_list.write`, `projectId` (in the PUT
  body; in the DELETE query or JSON body) and a client assignment to that project.
  When a base-image releasing project is configured, only that project may write.
  No additional opt-in is required for this API. The lists alter the effective
  components of subsequently created recipe versions. See
  [`mandatory_components_lists.py`](../../backend/app/packaging/entrypoints/s2s_api/routers/mandatory_components_lists.py).

## Base image channels and repair

Set Packaging and product-packaging `base-images.releasingProjectId` to the same
project ID to enable release channels (empty by default). In the same config,
`sourceOsVersion` is `Ubuntu 24`, `osVersion` is `Ubuntu 24.04 base`, `platform` is
`Linux`, `architectures` are `amd64` and `arm64`, and `parameterPrefix` is
`/vew/base-images` by default. The releasing project builds from the source OS,
then releases an image into `test` and subsequently `prod`. Recipes in any project
can select the corresponding test or production base-image OS entry. The release
updates SSM parameters; it does not rebuild every consumer image. The global
`GET /clients/packaging/v1/base-images[/{architecture}[/{channel}]]` uses
`clients/packaging/base_image.read`. `PUT .../base-images/{architecture}/{channel}`
uses `clients/packaging/base_image.write`, an image ID and the releasing project's
assigned client. The [channel model](../../backend/app/packaging/domain/model/recipe/base_image_channels.py),
[release command](../../backend/app/packaging/domain/command_handlers/image/release_base_image_command_handler.py)
and [S2S router](../../backend/app/packaging/entrypoints/s2s_api/routers/base_images.py)
enforce the sequence.

Packaging also repairs versions left `UPDATING` after an Image Builder create
failed before it produced an ARN. An hourly rule invokes
[`RetireStuckVersions`](../../backend/app/packaging/domain/command_handlers/shared/retire_stuck_versions_command_handler.py);
component and recipe versions without a build ARN can be retired without waiting
for a nonexistent Image Builder object. The schedule is in
[`packaging_app_stack.py`](../../backend/infra/backend/packaging_app_stack.py).
There is no deployment opt-in for this repair.

## Publishing automation and distribution

The **Publishing S2S API** adds project-scoped product creation, reads, updates
and archiving; version lists; and idempotent stage promotion. Grant
`clients/publishing/product.read`, `product.write`, `version.read` or
`version.promote` as needed, and assign the client to the project. The paths are
`/clients/publishing/projects/{projectId}/products`,
`/products/{productId}` and
`/products/{productId}/versions[/{versionId}/stages/{stage}]` under that base.
Creates require an `Idempotency-Key`; promotions return `202` while distributions
are in progress and can be polled by repeating the PUT. No feature flag enables
this API. See the [Publishing S2S API](../publishing-s2s-api.md) and
[`s2s_api/routers`](../../backend/app/publishing/entrypoints/s2s_api/routers).

The same API also adds `GET`, `PUT` and `DELETE`
`/clients/publishing/projects/{projectId}/products/{productId}/versions/{versionId}/retirement`.
Read needs `clients/publishing/version.read`; write needs
`clients/publishing/version.retire`. PUT starts retirement and returns `202` until
all distributions are retired, then `200`. It refuses a version with active
workbenches unless the caller explicitly sends `force`; DELETE only forgets the
declarative retirement and does not restore the version. See
[`retirements.py`](../../backend/app/publishing/entrypoints/s2s_api/routers/retirements.py).

Other optional distribution modes and controls:

- **Store and restore instead of sharing an AMI.** Set environment
  `image-distribution.mode` to `store-restore` (default `share`). The image service
  stores to the destination's import bucket; that account restores its own AMI.
  Re-onboard affected accounts so the import bucket and roles exist. Enable EBS
  encryption by default in each destination account. `storeWithFunctionRole` is
  `false` by default; use it only when the image service account is the function's
  own account and its role should start the store task. The sharing state machine
  and [`EC2ImageService`](../../backend/app/publishing/adapters/services/ec2_image_service.py)
  handle concurrent stores and inspect the destination bucket's object when EC2
  lists another account's task.
- **Several stages in one account.** Set environment
  `several-stages-per-account` to `true` (default `false`). An AWS account can
  then have distinct records for stages of one project, and Publishing keys
  portfolios and version distributions by account **and stage**. Existing
  Publishing records are migrated at API bootstrap. Keep the flag consistent
  across Projects and Publishing stacks. See
  [`accounts.py`](../../backend/app/projects/entrypoints/s2s_api/routers/accounts.py)
  and [Publishing migrations](../../backend/app/publishing/adapters/repository/dynamo_entity_migrations.py).
- **Platform products.** Set `platform-program-id` in both publishing and
  provisioning component config to the releasing project (empty disables it).
  A `PLATFORM` product is released once and distributed to every project's
  account at that stage; newly onboarded accounts catch up. Project products
  remain `PROGRAM` scoped by default. Creation uses the Publishing S2S product
  endpoint with `scope: "PLATFORM"` and its `product.write` scope. See
  [platform products](../platform-products.md) and
  [`distribute_platform_versions_command_handler.py`](../../backend/app/publishing/domain/command_handlers/distribute_platform_versions_command_handler.py).
- **Release-candidate limit.** Set publishing
  `retire-superseded-rc-versions` to `true` (default `false`) to retire old,
  non-recommended DEV-only candidates when a pipeline build would otherwise
  exceed `product-limit-rc-version` (default `2`). It does not retire promoted
  versions. See
  [`create_automated_version_event_handler.py`](../../backend/app/publishing/domain/event_handlers/create_automated_version_event_handler.py).
- **Build failure alarm.** A pipeline image that produces no product version
  emits a failure metric and raises a Publishing system-health alarm with the
  reason. No opt-in. See
  [`packaging_event_handler/handler.py`](../../backend/app/publishing/entrypoints/packaging_event_handler/handler.py)
  and [`publishing_app_stack.py`](../../backend/infra/backend/publishing_app_stack.py).

## Projects and access

The Projects S2S API goes beyond upstream's project listing, user assignments,
enrolments and service-client bindings. A management client can create, update and
deactivate projects through `POST /clients/projects/projects` and `GET`, `PUT`,
`DELETE /clients/projects/projects/{projectId}`. It can manage
`/projects/{projectId}/technologies[/{technologyId}]` and
`/projects/{projectId}/accounts[/{accountId}]` under the same base. The relevant
scopes are `clients/projects/program.read|write`,
`technology.read|write` and `account.read|write`. A project/client assignment is
also required for project-scoped routes. Project creation and account creation
use a UUID `Idempotency-Key`; account creation returns `202` and can be polled by
GET. Account updates resume interrupted onboarding, track `onboardingRevision`
and `onboardedAt`, and deactivation is guarded by account state. See
[`projects.py`](../../backend/app/projects/entrypoints/s2s_api/routers/projects.py),
[`technologies.py`](../../backend/app/projects/entrypoints/s2s_api/routers/technologies.py)
and [`accounts.py`](../../backend/app/projects/entrypoints/s2s_api/routers/accounts.py).

- **External management.** S2S `GET`, `PUT`, `DELETE`
  `/clients/projects/projects/{projectId}/management` mark a project as
  externally managed or hand it back to the portal. These use `program.read` or
  `program.write`. Portal writes to externally managed projects are refused;
  manage them through the S2S path instead. See
  [`externally_managed.py`](../../backend/app/shared/middleware/externally_managed.py).
- **Entra group bindings.** Grant `clients/projects/group_assignment.read|write`
  and use `GET /clients/projects/projects/{projectId}/groups` or `GET`, `PUT`,
  `DELETE .../groups/{groupId}` to assign roles to an Entra group, with its
  `groupName` for display. The authorizer combines trusted group membership and
  direct grants. The members list can show group members **after their first
  sign-in**, not before. The [group router](../../backend/app/projects/entrypoints/s2s_api/routers/groups.py)
  and [authorizer steps](../../backend/app/authorization/domain/services/auth/authorizer_steps.py)
  implement these checks.
- **Platform administrators and support.** Configure `platform-admin-groups`
  (empty by default) for both Projects and Authorization. Their Entra members
  receive platform-wide ADMIN access without per-project ADMIN assignments.
  The S2S user and group binding routes refuse an `ADMIN` grant. `SUPPORT` is a
  separate project role for remote support, not ordinary user access; the
  project's `remoteSupportEnabled` switch can disable support actions. See
  [`projects_auth.py`](../../backend/infra/auth/projects_auth.py) and
  [`authorizer_steps.py`](../../backend/app/authorization/domain/services/auth/authorizer_steps.py).
- **Private program inventory and workbench-only programs.** Set Projects
  `self-enrolment-enabled` to `false` (default `true`) to list only projects with
  a direct or group grant, except for platform admins. Set a project's
  `experience` to `workbench-only` through the Projects S2S project
  mutation to let members launch only workbenches from PROD releases. Members
  cannot manage Packaging or Publishing in this experience. See
  [`projects/entrypoints/api/handler.py`](../../backend/app/projects/entrypoints/api/handler.py)
  and [`product_provisioning_aggregate.py`](../../backend/app/provisioning/domain/aggregates/product_provisioning_aggregate.py).

## Workbench lifecycle and provisioning

Project shutdown policy inherits the deployment's `workbench-lifecycle` defaults.
The default idle timeout is 60 minutes, nightly shutdown is off, weekend shutdown
is on, and the nightly time is 21:00 UTC. Project administrators and program
owners can edit a portal-managed project's policy; owners can change their
workbench settings only where that policy permits. A policy can disable scheduled
stops or keep workbenches always on. Projects S2S `GET`, `PUT`, `DELETE`
`/clients/projects/projects/{projectId}/workbench-lifecycle` uses
`clients/projects/program.read|write`; the authenticated Projects API exposes
`GET`, `PUT`, `DELETE /projects/{projectId}/workbench-lifecycle`. The hub
reconciles effective settings and nightly/weekend stops. See the full
[shutdown and idle-stop guide](../idle-stop.md),
[`workbench_lifecycle.py`](../../backend/app/provisioning/domain/command_handlers/provisioned_product_state/workbench_lifecycle.py)
and [project policy model](../../backend/app/projects/domain/model/workbench_lifecycle.py).

Hub-side idle stop needs two **independent, off-by-default** switches:
provisioning `workbench-lifecycle.idleStop.enabled` and provisioning-enablement
`workbench-agents-enabled`. The latter delivers the report-only agent through
SSM on onboarding/re-onboarding; the hub reads DCV sessions, interactive sessions
and CPU load from CloudWatch. Missing signals keep a workbench running and raise
an alarm. Start with `idleStop.dryRun: true` before enabling real stops. Settings,
intervals, alarm behavior and rollout are in [idle-stop](../idle-stop.md).

Set provisioning `spoke-capacity.enabled` to `true` (default `false`) to collect
each spoke's EC2/EBS quotas, show capacity in the launch form and administration
pages, and reject launches that do not fit a fresh quota snapshot. Unknown or
stale data does not block launches. Platform admins can request increases; program
owners have read-only access to their own program. S2S adds
`GET /provisioning/capacity` (`clients/provisioning/capacity.read`) and
`PUT /provisioning/capacity/accounts/{awsAccountId}/quotas/{quotaCode}/increase`
(`clients/provisioning/capacity.quota_increase`). Re-onboard accounts to grant the
spoke role the needed permissions. See [spoke capacity](../spoke-capacity.md) and
[`spoke_capacity_domain_query_service.py`](../../backend/app/provisioning/domain/query_services/spoke_capacity_domain_query_service.py).

Without a flag, launch also checks which Availability Zones offer the requested
instance type, and treats EC2 `Unsupported` as a reason to try another eligible
zone like insufficient capacity. If the offerings lookup is unavailable, it
falls back to trying zones. See
[`ec2_instance_management_service.py`](../../backend/app/provisioning/adapters/services/ec2_instance_management_service.py)
and [the launch aggregate](../../backend/app/provisioning/domain/aggregates/product_provisioning_aggregate.py).
Launches store the owner's e-mail for operator capacity views, falling back to
the project assignment's e-mail. The user API also refreshes transitional
workbench status more frequently; see
[`status-refresh.ts`](../../frontend/web/src/components/pages/provisioned-products/status-refresh.ts).

## Deployment, web and CI

- **Multiple workbench regions.** `ENABLED_WORKBENCH_REGIONS` in
  [`config.env`](../../config.env) is a comma-separated list; it defaults to
  `us-east-1`. `deploy.sh` validates it and writes the environment's
  `enabled-workbench-regions` setting. Onboard the required spoke accounts and
  regions before using them. See [`deploy.sh`](../../deploy.sh) and
  [`backend/infra/config.py`](../../backend/infra/config.py).
- **Runtime frontend configuration.** The web app fetches `/aws-exports.json`
  before starting. [`configure_auth.sh`](../../frontend/web/configure_auth.sh)
  writes it into `public/` for deployment; the app no longer needs a
  build-time, environment-specific `aws-exports` module. See
  [`runtime-config.ts`](../../frontend/web/src/runtime-config.ts) and
  [`index.tsx`](../../frontend/web/src/index.tsx).
- **Program selector.** The header sorts programs by name and offers search
  instead of the upstream dropdown. No flag or API scope is needed. See
  [`program-select.tsx`](../../frontend/web/src/components/layout/page-header/program-select.tsx).
- **Repository checks.** [CI](../../.github/workflows/ci.yml) runs backend and
  frontend checks on GitHub-hosted runners by default; repository variable
  `CI_RUNNER=codebuild` selects CodeBuild. Pull requests check changed files
  against the full base history; push-time full scans and frontend dependency
  audits are reported without blocking. The separate
  [upstream sync workflow](../../.github/workflows/sync-awslabs.yml) touches
  `awslabs` only. None of these controls changes application API scopes.

## Differences from upstream behaviour

These changes affect the same paths as upstream. Check them especially when
rebasing:

| Area | Elva's additional or changed behavior | Code |
| --- | --- | --- |
| API Gateway nullable request fields | Upstream removes type constraints for `nullable` fields. Elva also strips array/object structural keywords (`items`, `properties`, `required` and related keywords) from the **deployed Gateway model** so Gateway does not infer a type and reject `null`. The published OpenAPI schema and Pydantic handler validation remain unchanged. | [`openapi_nullable.py`](../../backend/infra/constructs/openapi_nullable.py) |
| Provisioning boundaries | Launch checks project membership, permitted stage, an active account and project-scoped distribution on the server. Updates use the existing workbench's own account and Service Catalog product; downgrades are refused. The UI alone is not trusted to enforce these. | [`product_provisioning_aggregate.py`](../../backend/app/provisioning/domain/aggregates/product_provisioning_aggregate.py) |
| Publishing templates | The upstream destination image isolation remains. Elva additionally validates **rendered** Jinja product templates, with region/architecture inputs, and cleans up workbench-role resources for failed publishing. | [`product_template.py`](../../backend/app/publishing/domain/model/product_template.py), [`workbench_role_cleanup.py`](../../backend/infra/usecase/resources/workbench_role_cleanup.py) |
| Failure details | Upstream and Elva both classify launch/start failures. Elva also records update failures and polls transitional states. Unlike the upstream response mapper, Elva's mapper does **not** suppress raw `statusReason` for non-admin responses; do not assume that field is owner-safe. | [`api_model_mappers.py`](../../backend/app/provisioning/entrypoints/api/model/api_model_mappers.py) |
