# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added a project-scoped OAuth S2S API for declarative component and component-version publishing.
- Added external-ID mappings, optimistic revisions, idempotent reconciliation operations, and lifecycle event recovery.
- Added service-client project assignments and dedicated Packaging read, write, release, and operation scopes.

### Changed

- Component versions can be retired through the declarative API while released content remains immutable.

### Fixed

- API Gateway's request validator now accepts `null` for fields the OpenAPI schemas declare `nullable`; it validates against JSON Schema draft 4, which ignores `nullable`, and answered `400` before the request reached the handler.

## [0.0.0] - 2022-08-17

### Added

- Pre-release placeholder
