# DCV connections through a gateway: proposed design

Status: **proposal for review**. It brings together two pieces of work:

- **#4** (draft, `feat/dcv-gateway-frontend`): the portal flow for a DCV Connection Gateway.
- **Token connections in the Saab deployment**: a gateway per spoke account, short-lived signed
  tokens checked on the workbench, and a "desktop ready" signal. They have run in production since
  2026-09-30.

The aim is one design upstream: #4's portal contract, served by the backend, with a readiness gate.

## What #4 does today

- `AppConfig.DcvGatewayUrl`: a single gateway URL for the whole deployment.
- The portal calls `POST {DcvGatewayUrl}/api/dcv/connections` with the user's Cognito access token
  and `{projectId, provisionedProductId}`.
- Response: `{mode: "direct"}`, or `{mode: "gateway", gatewayUrl, sessionId, token}`. The
  `gatewayUrl` must have the configured gateway's origin.
- In `gateway` mode, the DCV browser and file logins connect to the gateway host and port with
  `sessionId` and `authToken`. Without a gateway URL, the existing direct flow is unchanged.

It leaves open who serves `/api/dcv/connections`, how tokens are issued and checked, and how the
gateway finds the workbench.

## What the Saab deployment runs

- **Connection details from the provisioning API.**
  `GET /projects/{projectId}/products/provisioned/{provisionedProductId}/dcv-connection`, Cedar action
  `GetProvisionedProductDcvConnection`, owner only. It returns:

  ```json
  {"host": "dcv-<spoke>.<zone>", "port": 8443, "sessionId": "<provisionedProductId>",
   "authToken": "<JWT>", "expiresAt": "<ISO 8601>", "webUrl": "https://host:port/?authToken=...#<sessionId>"}
  ```

- **One gateway per spoke account**, named `dcv-<spoke>.<zone>`. A workbench is only reachable from
  inside its own account, so a single deployment-wide gateway cannot reach every workbench.
- **Tokens.** ES256 JWTs signed with a KMS key in the hub, 180 s lifetime, which may be reused within
  that window. The claims are:
  - `iss`;
  - `aud` = the workbench's account id;
  - `sub` = the user id;
  - `username` = the workbench's local user;
  - `sid` = the session id, which is the provisioned product id;
  - `pp`;
  - `iat`, `exp`.

  The public keys are written to an SSM parameter in each spoke.
- **Token check.** The workbench runs a local `auth-token-verifier` for DCV. It checks the signature,
  `aud`, `exp` and `sid`, and maps the token to the local user. No DynamoDB lookup and no call back to
  the hub are needed.
- **Session resolver.** A sidecar in the gateway task resolves a `sessionId` to the workbench's private
  IP from EC2 tags in the spoke.
- **Desktop readiness.** A running instance is not a running desktop: DCV took about four minutes
  after `RUNNING` to accept a session, and earlier connects ended with "The connection has been
  closed".
  - The workbench agent tags its own instance `vew:dcv-ready=<UTC timestamp>` once the DCV session
    exists, `dcvserver` listens and the verifier is up.
  - It tags `false` at boot and when the session is lost; the template also sets `false` at launch.
    The instance role may tag only that key, and only on its own instance.
  - The hub reads the tag through EC2 events and the status sync, as `desktopReady` on the
    provisioned product. A timestamp older than the instance's `LaunchTime` counts as not ready.
  - The portal shows "Starting desktop…" instead of Connect until `desktopReady` is true.
  - The connection endpoint refuses with "The workbench desktop is still starting" until then.

## Proposal

1. **Keep #4's portal contract and serve it from the provisioning API.**
   - Add `POST /projects/{projectId}/products/provisioned/{provisionedProductId}/dcv-connections`
     (Cedar `GetProvisionedProductDcvConnection`, owner only). It returns #4's
     `{mode, gatewayUrl, sessionId, token}`, plus `expiresAt`.
   - The portal calls the provisioning API, which it is already authorized for, instead of an
     endpoint on the gateway, so the gateway stays a plain DCV Connection Gateway.
   - `mode: "direct"` only when the deployment has no gateway domain configured: today's flow. There
     is no per-project or per-product switch.
2. **Gateways per account.** `gatewayUrl` comes from the deployment's gateway naming
   (`dcv-{accountName or accountId}.{gatewayDomain}:{port}`).
   - #4's exact-origin check becomes a check against the configured gateway domain: `https:`, host
     ends with `.{gatewayDomain}`, and no path, query or credentials.
   - `AppConfig.DcvGatewayUrl` becomes `AppConfig.DcvGatewayDomain`. When it isn't set, the portal
     uses `direct`.
3. **Tokens and verification.** These are backend modules, not Saab configuration, so upstream them
   as is:
   - the signer port with its KMS adapter, and the token claims above;
   - the workbench verifier (`auth-token-verifier`) and the gateway's session resolver, as a reference
     implementation with an image-building component, since every deployment installs them in its
     own images and accounts.
4. **Desktop readiness as a refusal, not a mode.** While `desktopReady` is false the endpoint answers
   **HTTP 409** with `{"code": "DESKTOP_NOT_READY", "message": "The workbench desktop is still
   starting", "retryAfter": 15}` and a `Retry-After: 15` header. There is no `pending` mode, so #4's
   response contract is unchanged.
   - The portal gates Connect on `desktopReady` from the workbench status ("Starting desktop…"), so the
     409 is only a safety net, for a stale page or a direct API call. On a 409 the portal shows
     "Starting desktop…" again.
   - `desktopReady` is on the provisioned product, `null` for workbenches without the tag (older
     templates). The portal then behaves as today.
   - The portal's 5-second status polling (#7) turns Connect on as soon as it flips.
   - The tag key and the instance-role statement go into the default workbench template.
5. **Order of PRs.**
   1. The backend endpoint, signer and claims, with `mode: "direct"` until a gateway domain is
      configured.
   2. #4 rebased onto it: the backend call, and the domain check instead of the origin check.
   3. Desktop readiness: tag, sync, `desktopReady`, the 409 `DESKTOP_NOT_READY`, the portal's "Starting
      desktop…".
   4. The verifier, resolver and image component as optional reference parts.

## What stays deployment-specific

- Gateway hostnames and DNS.
- Certificates.
- The per-spoke edge infrastructure.
- The KMS key's location.
- Network paths to the gateways.

## Decisions (review 2026-10-01)

- **Not ready:** HTTP 409 `DESKTOP_NOT_READY` with `retryAfter` and a `Retry-After` header, not an HTTP
  200 `pending` mode. The portal's own gate on `desktopReady` makes the 409 a safety net only.
- **Tokens:** 180 s lifetime, reusable within that window (the DCV web client opens several channels
  with the same token). No single-use tokens, so no store of used `jti`s.
- **Direct mode:** only when no gateway domain is configured. No per-project or per-product switch.
