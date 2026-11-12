# Project roles from OIDC groups

Group grants let people use projects through their identity provider's groups.
Each project binds a group id to existing project roles. Effective roles are
the union of direct assignments and group bindings. At sign-in, a linked
federated user's trusted groups also create real project user assignments.
These assignments appear on the Members page and use the existing membership
events and provisioning lifecycle.

## Enable group grants

Configure OIDC sign-in as usual. Set `OIDC_GROUPS_CLAIM` in `config.env` to the
claim containing the groups, for example `groups`, and run `deploy.sh`.
Leave it empty to disable group grants and keep direct-assignment behavior.
Deploy the frontend infrastructure before the backend, as the deployment script
does. Sign in again after enabling the setting.

For deployments made directly with CDK, set `OIDCGroupsClaim` alongside
`OIDCSecretName` in the frontend environment configuration. Set
`oidc-groups-enabled` to `True` in the backend environment configuration.
The frontend exports the actual OIDC provider name to SSM. Both backend role
resolvers use it; there is no separate provider name to maintain.

Cognito maps the claim to the mutable `custom:oidc_groups` attribute. It accepts
JSON arrays and Cognito's flattened multi-value representation. Group ids are
opaque, case-sensitive strings, not necessarily UUIDs. The attribute has
Cognito's 2,048-character limit. Limit the groups emitted by the provider to
those the application needs.

Only profiles with a linked identity for the configured OIDC provider can
receive group roles. A native Cognito user cannot gain group access by writing
the custom attribute. Do not remove app clients' write access to the attribute:
Cognito needs it for federated attribute mapping.

## Manage bindings

The regular projects API exposes `GET`, `PUT`, and `DELETE` on
`/projects/{projectId}/groups`. These actions use the same authorization rule
as changing user roles: program owners and admins can manage bindings.

- `GET` returns `{ "assignments": [...] }` with active bindings.
- `PUT` accepts `{ "groupId": "engineering/team", "groupName": "Engineering", "roles": ["PLATFORM_USER"] }`.
  It replaces the roles and optional label and returns the saved binding.
  Omit `groupName` or use `null` to clear the label. An empty roles list grants nothing.
- `DELETE` accepts `{ "groupId": "engineering/team" }` and returns `{}`.
  The id is in the body so slashes and other URL characters need no special handling.

Roles are `ADMIN`, `PROGRAM_OWNER`, `POWER_USER`, `PRODUCT_CONTRIBUTOR`,
`BETA_USER`, and `PLATFORM_USER`. The label is for display only.
`GET /projects` keeps its existing paging and response fields; its `assignments`
include effective roles for listed projects when groups are present.

### Web interface

Program owners and admins can manage bindings in the web app under
**Members**, on the **Groups** tab. The tab lists each group's ID, display name
and roles, and has actions to add, edit and remove a binding. Editing changes the
display name and roles; the group ID is read-only. Removing asks for
confirmation. The tab is hidden when group grants are disabled
(`OIDC_GROUPS_CLAIM` is empty) and for users who cannot manage roles. Program
owners cannot pick the Program Owner or Frontend Admin roles, as in the
members table. The form needs at least one role.

The Members tab marks group-sourced members with **via group <id>** for each
granting group. Adding a direct assignment or updating a member's roles makes
the assignment direct. Later group changes do not change a direct assignment.

Binding changes reach the authorizer through versioned events. Reconciliation
repairs missed events, including deletions. Removing a binding does not remove
direct roles or roles granted by another group.

## Membership changes and limits

On sign-in, Cognito's pre-token trigger queues membership reconciliation.
Token refreshes do not queue it. This applies to every app client, not just the
web app. Membership updates are asynchronous and can take a short time to appear.
Deploy both frontend and backend before signing in: the frontend trigger queues
work to the backend membership function. A queueing failure fails sign-in;
backend processing failures use the function's existing retries and failure queue.

Reconciliation reads the latest Cognito profile and checks its linked provider
identity before trusting groups. It creates group-sourced assignments with the
union of the bound groups' roles, updates changed roles, and removes assignments
that no bound group still grants. Each assignment stores `source: GROUP`, the
granting `groupIds`, and the last trusted `trustedGroupIds` snapshot. Existing
records without a source are direct. Direct assignments are never changed by
reconciliation. Changing or deleting a binding reconciles that project's
group-sourced members using these stored group lists, without an IdP lookup.

Assignment creation, role updates, and removal use the regular command handlers
and their `UserAssigned`, `UserReAssigned`, and `UserUnAssigned` events. Removal
runs provisioning's existing workbench removal and user profile and maintenance
window cleanup. This also applies when an admin removes a group-sourced member
by hand. Binding edits do not recreate a manually removed membership; the next
sign-in can restore it. Workbenches removed during cleanup are not restored.

Removing membership is not a group-access block. Binding-based effective roles
remain in the authorizer, projects list, and internal launch checks. This keeps
access available while asynchronous membership events are being processed.
Remove the binding or disable the account to stop group access. The removal
dialog warns about this and about membership returning at the next sign-in.

Removing someone from a group takes effect at their next sign-in. A user who
leaves a group but never signs in again keeps their membership until an admin
removes it or the account is disabled. Refreshing tokens is not a new sign-in.
Launch checks read the trusted groups stored in
the user's Cognito record from their last sign-in. Existing sessions and
authorization caches may retain earlier access until they expire; use sign-out
or disable the account when immediate revocation is needed.

Microsoft Entra omits the groups claim from tokens when a user belongs to more
than about 200 groups. Group-overage references are not resolved by this MVP.
If the claim is missing, no group roles are granted from that profile; direct
assignments still work. Configure application-specific groups to avoid overage.
