# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Platform-admin groups: members of the Entra groups in the `platform-admin-groups` config are `ADMIN` on every project without a grant per project (authorizer, project list, server-side role checks). The group list is returned with a project's groups.
- `self-enrolment-enabled` (default `true`): when `false`, users see only the projects they hold a role on; platform admins see all.
- Added a project-scoped OAuth S2S API for declarative component and component-version publishing.
- Added external-ID mappings, optimistic revisions, idempotent reconciliation operations, and lifecycle event recovery.
- Added service-client project assignments and dedicated Packaging read, write, release, and operation scopes.
- Added a project-scoped OAuth S2S Publishing API for products (create, update, archive) and product version promotion to a stage, with `clients/publishing` product and version scopes.

### Changed

- Server-side role checks (the internal user-assignment route that launch reads, and `GET /projects/{projectId}/users/{userId}`) include the roles Entra group grants give, read from the user's Cognito record; a user reached only through a group could not launch.
- The authorizer passes the sign-in's trusted Entra groups on as `userGroups`, so the Projects API needs no second UserInfo call.
- Project member lists fill a missing email or display name from the identity provider on read.
- Component versions can be retired through the declarative API while released content remains immutable.

### Fixed

- Stage access is enforced by the backend: a role may list and launch only the stages it may consume (`PLATFORM_USER` PROD, `BETA_USER` QA and PROD, contributors and above all stages). It used to shape only the product listing, so a direct API call could reach DEV or QA releases.
- Launching refuses a version whose project account is not `Active`.
- Product version listings return only versions of the project in the path.
- API Gateway's request validator now accepts `null` for fields the OpenAPI schemas declare `nullable`; it validates against JSON Schema draft 4, which ignores `nullable`, and answered `400` before the request reached the handler.

## [0.0.0] - 2022-08-17

### Added

- Pre-release placeholder
