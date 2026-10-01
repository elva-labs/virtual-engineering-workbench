# Platform products

A **platform product** is built, tested and promoted once by one project - the *releasing project* -
and distributed to every project's accounts. Typical use: a default workbench every project offers,
whose lifecycle a central platform team owns, while each project keeps its own products.

Switched off unless a deployment names the releasing project: `platform-program-id` in the publishing
and provisioning component config (`backend/infra/config.py`).

## How it works

- **Scope.** A product has a `scope`: `PROGRAM` (the default, a project's own product) or `PLATFORM`.
  Only the releasing project creates `PLATFORM` products - Publishing S2S
  `POST /projects/{id}/products {..., scope: "PLATFORM"}`, `409 RELEASING_PROJECT_ONLY` for any other
  project. The scope cannot change.
- **Distribution follows the stage.** A program product's version goes to the portfolios of the
  product's technology. A platform product's version goes to **every project's portfolio at that
  stage**: when it is created (DEV), promoted (QA, PROD) or restored. Retire, unpublish and archive
  act on the version's distributions as before, so they reach every project.
- **New accounts catch up.** When a project account is onboarded and its portfolio is ready, it
  receives every platform version that is live at its stage.
- **Every project lists and launches it.** Provisioning adds the releasing project's platform
  products to every project's product list and lookups. Listing versions, launching and updating
  use the distribution in the project's **own** account for the stage. The portal marks the product
  as *Platform*.
- **Rules unchanged.** Stage access (plain users launch PROD only), the active-account check, the
  externally-managed lock and workbench ownership apply as for any product. A project cannot edit a
  platform product.

## Limits

- No per-project holdback: every project receives a new platform version at the same time.
- A project without an account at a stage doesn't see the platform product there.
