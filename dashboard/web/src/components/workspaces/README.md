# Workspace UI integration

`WorkspaceManager` is an additive workspace screen. It calls the real `/v1` workspace API through `workspace-client.ts`; it does not create a login system, seed a session, select a demo port, or import a ProductView fixture. Existing app/router/store/session/fence/wipe code remains unchanged.

The host owns authentication, HTTPS origin, routing, capabilities and context. Render this screen **before** the existing `ServiceProvider` requires `useView`; a person with `needs_workspace` does not yet have a ProductView. Do not mount it beneath `App`, which reads `useView()` unconditionally.

```tsx
import { WorkspaceManager, type WorkspaceManagerProps } from "./components/workspaces/WorkspaceManager";

// The authenticated host supplies this object from its current server facts.
const props: WorkspaceManagerProps = {
  session,                       // existing readSession output; includes in-memory csrf_token
  workspace: capabilities,        // existing readCapabilities output, or null
  canCreateWorkspace: session.can_create_workspace === true, // current producer hint
  fetch: (url, init) => fetch(url, init),
  signal: contextAbort.signal,
  isCurrent: () => fence.current(ticket),
  onSessionRefresh: refreshSession,
  onCapabilitiesRefresh: refreshCapabilities,
  onWorkspaceSelected: selectWorkspace,
  onAuthError: handleConfirmedAuthError,
  onPrivateStateCleared: clearWorkspaceDrafts,
};
<WorkspaceManager {...props} />;
```

`canCreateWorkspace` defaults to false. The host passes the current shared Session `can_create_workspace` boolean; a missing legacy field remains false. That hint uses the same server gate as `POST /v1/workspaces`: provisioned, verified, enabled, needs-workspace and no active membership. The mutation checks current facts again. The additive onboarding-hint endpoint was withdrawn and does not exist. Never infer authority from a role, email or email domain. Production self-creation policy and joint acceptance remain with the project owners.

`isCurrent` is required and fails closed if missing. Supply the host's current actor/context/workspace ticket predicate; cancellation alone cannot discard a late response after its JSON has resolved.

## Host callbacks and lifecycle

- `onSessionRefresh()` and `onCapabilitiesRefresh(workspaceId)` fetch current server facts and reject with the existing `ProductError` when access cannot be confirmed. They must apply the host's own account/context fence after awaits; old callbacks must not replace a new account or workspace. The component invokes both after a successful mutation (a selection reads the chosen workspace's capabilities through the host instead), after a refused change, and from its Try again / Refresh access buttons. It runs no timer and no focus/visibility listener of its own: timed and on-return checks belong to the host's session guard, which already reads session and capabilities within the four-second lease. Calls are deduplicated inside the screen.
- Creation/acceptance supply the returned workspace ID to `onWorkspaceSelected`. The refresh calls are started in the same turn before selection changes. The host may update capabilities for that explicitly selected target; every response remains bound to its actor/context and requested ID.
- The host owns `onAuthError`: confirmed session_expired/session_revoked/membership_revoked only. Use existing guard/wipe/fence behavior to clear private work and choose the next screen; ordinary permission denial, wrong invitation account or CAS conflict is not logout.
- Actor, context, workspace, role, membership revision and CSRF changes synchronously remount the inner screen, removing previous member data, invitation tokens and form drafts before the next view. An external abort also clears/hides private state. Cleanup aborts requests; unmount calls `onPrivateStateCleared`. This callback clears UI drafts, not the server session.
- The screen preserves invitation/account form values only in memory. Tokens are neither URL parameters nor storage values. Issued tokens render in a password input for deliberate copying; clear/revoke removes them. Do not include those input values in screenshots, logs or browser test output.
- A 412 refreshes current session/capabilities and the first member page, shows a conflict, and never repeats the write. A lost mutation response remains `outcome_unknown`; current state is refreshed before the person decides what to do next. The server's last-owner/role/object checks remain authoritative.
- Workspace selection first reads the exact selected workspace from the server. Workspace, member, pending-invitation and exact-email-match pages all use opaque signed cursors; changing context or the lookup email discards older responses. Member display names come from server rows; a missing legacy-fixture name is represented as null and the UI shows the account ID, never a fabricated name.
- Invitation lookup posts the exact email in JSON, never a URL. Multiple matching verified accounts stay distinct. A person must pick a returned account before Create invitation is enabled; the client never links accounts or invites an arbitrary first match. Only the selected user_id is sent to invitation creation.
- Pending invitation pages contain safe names/role/expiry/IDs only, with no token/hash. The client rejects those secret fields if received. Managers can revoke an existing pending invitation without recovering its token. Only the creation response's token is available in this tab until cleared/revoked/context change.

The host must cover private content while it cannot confirm current access and revalidate before showing a restored/bfcache page. In the hosted app that is SessionGate, which covers this screen on return until the guard's check answers; this screen covers its own contents only while its own revalidation (after a refused change, or from its buttons) is running. A hidden/throttled tab is not a background ≤5-second browser guarantee; test the actual mounted journey and focus recovery.

## API boundary

| Client method | Request |
|---|---|
| workspaces({limit=50,cursor}) | GET `/v1/workspaces?limit=…&cursor=…` |
| readWorkspace(workspaceId) | GET `/v1/workspaces/{workspace}` |
| create(name) | POST `/v1/workspaces` |
| acceptInvitation(token) | POST `/v1/invitations/accept` |
| members({limit=50,cursor}) | GET `/v1/workspaces/{workspace}/members?limit=…&cursor=…` |
| pendingInvitations({limit=50,cursor}) | GET `/v1/workspaces/{workspace}/invitations?limit=…&cursor=…` |
| lookupInvitationTargets(email,{limit=20,cursor}) | POST `/v1/workspaces/{workspace}/invitation-targets`; exact email/cursor stay in JSON |
| changeMember(userId, change, revision) | PATCH member; strong If-Match |
| transferOwner(userId, revision) | POST transfer-owner; strong If-Match target revision |
| invite(userId, role, expiresInSeconds=86400) | POST invitation; owner is excluded |
| revokeInvitation(invitationId) | DELETE invitation; JSON `{}` |
| setSourceGrant(sourceId,userId,actions,revision) | PUT source grant; strong If-Match |
| sourceGrant(sourceId,userId) | GET source grant; explicit `actions:[]`, `revision:0` when absent |

All mutations send the current in-memory `X-CSRF-Token`; cookies use same-origin fetch. The API origin is fixed by relative routes. Bodies contain target IDs and requested changes, never an acting user or claimed acting role. Responses are validated for version, exact workspace/target and relevant mutation outcome. Member pages require `next_cursor` and a maximum of the requested number of members; totals are not invented. Cursor values remain opaque and are URL encoded.

Invitation Problems are read only with exact status pairs: 410 invitation_expired, 404 invitation_unavailable, 403 invitation_wrong_account. Error text shown to people is fixed local copy, never raw server/parser/transport diagnostics or an invitation's target email. Unknown or mismatched Problem/status pairs fail closed.

## Verification scope

Run the installed client tests from `dashboard/web` with the exact Node 22 command:

```text
node --experimental-strip-types --test tests/workspace-client.test.mjs
```

The flag is required because the test imports the actual TypeScript boundary. No package.json change or new dependency is needed. The current 14 Node tests exercise real boundary code with controlled transport: all original eight operations plus five directory/grant reads, CSRF/CAS, cursor encoding, exact-email ambiguity without auto-invitation, safe pending lists, scope/target/schema rejection, exact invitation errors, no retries, abandoned requests and stale auth responses. A no-emit TypeScript virtual overlay was checked against the current full frontend source without changing existing files. These checks do not constitute product browser acceptance: J-01's host mount, live PostgreSQL/IdP/browser journey and two-person review remain separate work.
