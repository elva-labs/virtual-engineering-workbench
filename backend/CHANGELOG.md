# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- API Gateway request validation accepts null for nullable OpenAPI fields, including arrays and objects.
- Component and recipe tests treat an SSM command that has not been registered yet as pending.
- Late component tests preserve released and retired versions; parallel association updates use conditional writes and retry transaction conflicts.
- Failed DynamoDB migrations persist their failure state and raise instead of starting against a partially migrated table.
- Product distributions select their own account's image and store rendered templates separately by account and stage.
- Product templates are validated after rendering; the first automated version is created as 1.0.0-rc.1 and template image mappings include available regions.
- Workbenches join the spoke workbench security group, and removal cleans up policies attached to their instance role outside the product template.
- Launches choose availability zones offering the requested instance type and retry EC2 Unsupported errors like capacity failures.
- The portal explains launch and start failures and preselects the recommended or newest released product version.

## [0.0.0] - 2022-08-17

### Added

- Pre-release placeholder
