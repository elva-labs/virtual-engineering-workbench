# Publishing service-to-service API

The Publishing S2S API lets a service client manage a project's products and release product
versions to a stage without the portal. It is a thin layer over the existing Publishing commands
and uses the existing internal IDs. Pipelines already link to a product through the Packaging S2S
API (`productId`); every image a pipeline builds adds a product version in `DEV`. With this API a
client can create that product and promote a tested version to `QA` or `PROD`.

It follows the same rules as the [Packaging S2S API](packaging-s2s-api.md): OAuth client
credentials, a project assignment for the client, `Idempotency-Key` on creates, and
`application/problem+json` errors.

## Prerequisites

The OAuth client needs the scopes of the operations it uses:

- `clients/publishing/product.read` to read and list products
- `clients/publishing/product.write` to create, update and archive products
- `clients/publishing/version.read` to list product versions and read a promotion
- `clients/publishing/version.promote` to promote a version to a stage

The client must also be assigned to every project it accesses (see
[Assign the client to a project](packaging-s2s-api.md#assign-the-client-to-a-project)). A request
for an unassigned project returns `403 PROJECT_ACCESS_DENIED` before Publishing looks up the
requested resource.

Paths below are relative to `/clients/publishing/projects/{projectId}`.

## Products

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` / `POST` | `/products` | List / create products |
| `GET` / `PUT` / `DELETE` | `/products/{productId}` | Read, update name and description, or archive |

Create a product with `POST /products` and an RFC 4122 UUID `Idempotency-Key` header:

```json
{
  "productName": "Example workbench",
  "productType": "WORKBENCH",
  "productDescription": "Managed by a service client",
  "technologyId": "tech-example"
}
```

`productType` is `WORKBENCH`, `VIRTUAL_TARGET` or `CONTAINER`. `technologyId` must be a
technology of the project: Publishing looks it up in Projects and stores its name, and an unknown
technology returns `422 TECHNOLOGY_NOT_FOUND`. Names allow 1 to 50 and descriptions up to 100
letters, digits, spaces, hyphens and underscores. The response is `201` with
`{"productId": "prod-..."}`.

The idempotency rules are the Packaging ones: an identical retry with the same key replays the
stored response, the same key with another body returns `409 IDEMPOTENCY_KEY_REUSED`, a request
whose first attempt is still running returns retryable `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` with
`Retry-After: 5`, and a validation failure is stored and replayed for the key.

`GET /products/{productId}` returns the product with its `status`, `technologyName`,
`availableStages` and `recommendedVersionId`; `GET /products` returns `{"products": [...]}`.
Archived products are still returned, with status `ARCHIVING` or `ARCHIVED`. A product of another
project returns `404`.

`PUT /products/{productId}` changes `productName` and `productDescription` only; a product's type
and technology are fixed. It returns `200` with the product, or `409 RESOURCE_CONFLICT` once the
product is archiving or archived.

`DELETE /products/{productId}` archives the product, which unpublishes every version from every
account. It returns `202` with `Retry-After: 5` while archiving and `204` once the product is
archived or does not exist, so a client repeats it until `204`.

## Product versions and promotion

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/products/{productId}/versions` | List versions with their stages |
| `GET` / `PUT` / `DELETE` | `/products/{productId}/versions/{versionId}/stages/{stage}` | Read, promote, or forget a promotion |

`GET .../versions` returns `{"versions": [...]}`; each version has `versionId`, `versionName`,
`versionType` and `stages`, a list of `{stage, status}` aggregated over the version's accounts in
that stage.

`PUT .../versions/{versionId}/stages/{stage}` (no body) promotes the version to `DEV`, `QA` or
`PROD` (case-insensitive). It returns `202` with `Retry-After: 30` and the promotion while the
stage's distributions are being created, and `200` once all of them are `CREATED`. Repeating the
PUT returns the current state and never promotes twice, so a client polls with the same request.
The response has `versionName`, `stage`, `status` and `distributions` (`awsAccountId`, `region`,
`status`).

The portal's rules apply, with the service client acting as `PROGRAM_OWNER`: every distribution of
the version must be `CREATED`, `PROD` accepts only release candidates (the version then gets its
release name, for example `1.0.0-rc.2` becomes `1.0.0`), and the stage needs an onboarded account.
A rule violation returns `422 DOMAIN_VALIDATION_FAILED`.

`GET .../stages/{stage}` returns the promotion, or `404` until the version is in the stage.
`DELETE .../stages/{stage}` returns `204` and changes nothing: a release is not undone. Retire a
version to withdraw it.
