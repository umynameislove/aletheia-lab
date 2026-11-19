/* Workspace API boundary. Cookies stay with fetch; CSRF and invitation tokens stay in memory. */
import { PRODUCT_API_VERSIONS, ROLES, type HostedSession } from "./hosted.ts";
import { ProductError } from "./port.ts";

export type WorkspaceRole = typeof ROLES[number];
export type InviteRole = Exclude<WorkspaceRole, "owner">;
export type GrantAction = "read" | "write" | "review" | "export" | "delete";
export interface WorkspaceValue {
  workspace_id: string; workspace_name: string;
  membership: { role: WorkspaceRole; revision: number };
}
export interface WorkspaceMember {
  workspace_id: string; user_id: string; display_name: string | null; role: WorkspaceRole; active: boolean; revision: number;
}
export interface WorkspacePage { items: WorkspaceValue[]; next_cursor: string | null }
export interface WorkspaceMemberPage { workspace_id: string; items: WorkspaceMember[]; next_cursor: string | null }
export interface InvitationTarget { user_id: string; display_name: string }
export interface InvitationTargetPage { workspace_id: string; items: InvitationTarget[]; next_cursor: string | null }
export interface PendingInvitation extends InvitationTarget {
  workspace_id: string; invitation_id: string; role: InviteRole; expires_at: string;
}
export interface PendingInvitationPage { workspace_id: string; items: PendingInvitation[]; next_cursor: string | null }
export interface WorkspaceInvitation {
  workspace_id: string; invitation_id: string; user_id: string; role: InviteRole;
  expires_at: string; token: string;
}
export interface WorkspaceSourceGrant {
  workspace_id: string; source_id: string; user_id: string; actions: GrantAction[]; revision: number;
}
export type WorkspaceFetch = (url: string, init: RequestInit) => Promise<Pick<Response, "status" | "json">>;
export interface WorkspaceClientOptions {
  session: HostedSession; workspaceId: string | null; fetch: WorkspaceFetch;
  signal?: AbortSignal; isCurrent: () => boolean; onAuthError?: (error: ProductError) => void;
}

export class WorkspaceRequestDiscarded extends Error {
  constructor() { super("This workspace request belongs to an earlier context."); this.name = "WorkspaceRequestDiscarded"; }
}

type Obj = Record<string, unknown>;
const obj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const text = (value: unknown, maximum: number): value is string => typeof value === "string" && [...value].length > 0 && [...value].length <= maximum;
const revision = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const role = (value: unknown): value is WorkspaceRole => (ROLES as readonly unknown[]).includes(value);
const invalid = () => new ProductError("response_invalid", "The service sent a workspace answer this app cannot trust.");
function need(condition: boolean): asserts condition { if (!condition) throw invalid(); }
function envelope(value: unknown): Obj {
  need(obj(value) && typeof value.schema_version === "string" && PRODUCT_API_VERSIONS.includes(value.schema_version));
  return value;
}
function inWorkspace(value: unknown, workspace: string): Obj {
  const answer = envelope(value);
  need(answer.workspace_id === workspace);
  return answer;
}
function workspace(value: unknown): WorkspaceValue {
  const x = envelope(value), membership = x.membership;
  need(identifier(x.workspace_id) && text(x.workspace_name, 256) && obj(membership)
    && role(membership.role) && revision(membership.revision));
  return { workspace_id: x.workspace_id, workspace_name: x.workspace_name,
    membership: { role: membership.role, revision: membership.revision } };
}
function member(value: unknown, scope: string, user?: string): WorkspaceMember {
  const x = inWorkspace(value, scope);
  need(identifier(x.user_id) && (user === undefined || x.user_id === user)
    && (x.display_name === undefined || x.display_name === null || text(x.display_name, 256))
    && role(x.role) && typeof x.active === "boolean" && revision(x.revision));
  return { workspace_id: scope, user_id: x.user_id, display_name: typeof x.display_name === "string" ? x.display_name : null,
    role: x.role, active: x.active, revision: x.revision };
}
function pageFields(x: Obj, limit: number): { items: unknown[]; next_cursor: string | null } {
  need(Array.isArray(x.items) && x.items.length <= limit && (x.next_cursor === null || text(x.next_cursor, 4096)));
  return { items: x.items, next_cursor: x.next_cursor as string | null };
}
function pageQuery(limit: number, cursor: string | undefined, maximum = 200): string {
  if (!Number.isInteger(limit) || limit < 1 || limit > maximum || (cursor !== undefined && !text(cursor, 4096))) {
    throw new ProductError("invalid_request", "A valid page is required.");
  }
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor !== undefined) query.set("cursor", cursor);
  return query.toString();
}
function instant(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return year > 0 && days !== undefined && day > 0 && day <= days && hour < 24 && minute < 60 && second < 60
    && (match[7] === undefined || (Number(match[8]) < 24 && Number(match[9]) < 60));
}

const PROBLEMS: Readonly<Record<string, [number, string]>> = {
  session_expired: [401, "Your session has ended. Sign in again."],
  session_revoked: [401, "This session is no longer active."],
  membership_revoked: [403, "Your workspace access has ended."],
  permission_denied: [403, "You do not have permission to do that."],
  csrf_invalid: [403, "Your request could not be verified. Refresh your session before trying again."],
  revision_conflict: [412, "This membership changed. The latest state has been loaded; review it before trying again."],
  precondition_required: [428, "Refresh the membership before changing it."],
  invitation_expired: [410, "This invitation has expired. Ask a workspace manager for a new one."],
  invitation_unavailable: [404, "This invitation is unavailable or has already been used."],
  invitation_wrong_account: [403, "Sign in with the account this invitation was issued to."],
  not_found: [404, "The requested resource is unavailable."],
  schema_invalid: [422, "Check the form values before trying again."],
  invalid_request: [400, "The request could not be accepted. Refresh and check the form values."],
  cursor_invalid: [400, "This member page is no longer valid. Return to the first page."],
  cursor_expired: [410, "This member page has expired. Return to the first page."],
  service_unavailable: [503, "The service is temporarily unavailable. Refresh before trying again."],
};

function problem(value: unknown, status: number): ProductError {
  if (!obj(value) || typeof value.code !== "string") return invalid();
  const known = PROBLEMS[value.code];
  if (!known || known[0] !== status || value.status !== status || !text(value.detail, 1000)
    || !text(value.type, 256) || !text(value.title, 256) || !identifier(value.request_id)
    || typeof value.schema_version !== "string" || !PRODUCT_API_VERSIONS.includes(value.schema_version)) return invalid();
  return new ProductError(value.code, known[1]);
}

export function createWorkspaceClient(options: WorkspaceClientOptions) {
  const { session, workspaceId, fetch: requestFetch, signal, isCurrent, onAuthError } = options;
  const user = session.user_id, context = session.context_id, csrf = session.csrf_token;
  const current = () => !signal?.aborted && typeof isCurrent === "function" && isCurrent() === true && session.user_id === user && session.context_id === context;
  const check = () => { if (!current()) throw new WorkspaceRequestDiscarded(); };
  const scope = () => {
    if (!identifier(workspaceId)) throw new ProductError("invalid_request", "Select a workspace first.");
    return workspaceId;
  };
  const expectedRevision = (value: number) => {
    if (!revision(value)) throw new ProductError("invalid_request", "A valid membership revision is required.");
    return `"${value}"`;
  };
  const userId = (value: string) => {
    if (!identifier(value)) throw new ProductError("invalid_request", "Enter a valid account identifier.");
    return value;
  };
  async function request<T>(path: string, method: string, decode: (value: unknown) => T,
                            body?: object, ifMatch?: number, status = 200, changesState = method !== "GET"): Promise<T> {
    check();
    const mutation = method !== "GET";
    const headers: Record<string, string> = { Accept: "application/json" };
    if (mutation) { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = csrf; }
    if (ifMatch !== undefined) headers["If-Match"] = expectedRevision(ifMatch);
    let response;
    try {
      response = await requestFetch(path, { method, headers, credentials: "same-origin", cache: "no-store", signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    } catch (error) {
      if (error instanceof WorkspaceRequestDiscarded || (obj(error) && error.name === "AbortError")) throw new WorkspaceRequestDiscarded();
      check();
      throw new ProductError(changesState ? "outcome_unknown" : "unavailable", changesState
        ? "The change could not be confirmed. Refresh the latest state before trying again."
        : "The workspace could not be loaded. Try again.");
    }
    check();
    let value: unknown;
    try { value = await response.json(); } catch (error) {
      if (error instanceof WorkspaceRequestDiscarded || (obj(error) && error.name === "AbortError")) throw new WorkspaceRequestDiscarded();
      check(); throw invalid();
    }
    check();
    if (response.status !== status) {
      const error = problem(value, response.status);
      if (["session_expired", "session_revoked", "membership_revoked"].includes(error.code)) onAuthError?.(error);
      throw error;
    }
    return decode(value);
  }
  const base = () => `/v1/workspaces/${scope()}`;
  return {
    workspaces: ({ limit = 50, cursor }: { limit?: number; cursor?: string } = {}) => request(
      "/v1/workspaces?" + pageQuery(limit, cursor), "GET", value => {
        const page = pageFields(envelope(value), limit), items = page.items.map(workspace);
        need(new Set(items.map(item => item.workspace_id)).size === items.length);
        return { items, next_cursor: page.next_cursor } as WorkspacePage;
      }),
    readWorkspace: (workspaceId: string) => request("/v1/workspaces/" + userId(workspaceId), "GET", value => {
      const selected = workspace(value); need(selected.workspace_id === workspaceId); return selected;
    }),
    create: (name: string) => request("/v1/workspaces", "POST", value => {
      const created = workspace(value); need(created.membership.role === "owner"); return created;
    }, { name }, undefined, 201),
    acceptInvitation: (token: string) => request("/v1/invitations/accept", "POST", workspace, { token }),
    members: ({ limit = 50, cursor }: { limit?: number; cursor?: string } = {}) => {
      const selected = scope();
      return request(base() + "/members?" + pageQuery(limit, cursor), "GET", value => {
        const page = pageFields(inWorkspace(value, selected), limit);
        const items = page.items.map(item => member(item, selected));
        need(new Set(items.map(item => item.user_id)).size === items.length);
        return { workspace_id: selected, items, next_cursor: page.next_cursor } as WorkspaceMemberPage;
      });
    },
    pendingInvitations: ({ limit = 50, cursor }: { limit?: number; cursor?: string } = {}) => {
      const selected = scope();
      return request(base() + "/invitations?" + pageQuery(limit, cursor), "GET", value => {
        const page = pageFields(inWorkspace(value, selected), limit);
        const items = page.items.map(value => {
          const x = inWorkspace(value, selected);
          need(identifier(x.invitation_id) && identifier(x.user_id) && text(x.display_name, 256)
            && role(x.role) && x.role !== "owner" && instant(x.expires_at)
            && !("token" in x) && !("token_digest" in x) && !("token_hash" in x));
          return { workspace_id: selected, invitation_id: x.invitation_id, user_id: x.user_id,
            display_name: x.display_name, role: x.role, expires_at: x.expires_at } as PendingInvitation;
        });
        need(new Set(items.map(item => item.invitation_id)).size === items.length);
        return { workspace_id: selected, items, next_cursor: page.next_cursor } as PendingInvitationPage;
      });
    },
    lookupInvitationTargets: (email: string, { limit = 20, cursor }: { limit?: number; cursor?: string } = {}) => {
      const selected = scope();
      pageQuery(limit, cursor, 20);
      return request(base() + "/invitation-targets", "POST", value => {
        const page = pageFields(inWorkspace(value, selected), limit);
        const items = page.items.map(value => {
          need(obj(value) && identifier(value.user_id) && text(value.display_name, 256));
          return { user_id: value.user_id, display_name: value.display_name };
        });
        need(new Set(items.map(item => item.user_id)).size === items.length);
        return { workspace_id: selected, items, next_cursor: page.next_cursor } as InvitationTargetPage;
      }, { email, limit, ...(cursor === undefined ? {} : { cursor }) }, undefined, 200, false);
    },
    changeMember: (target: string, change: { role?: WorkspaceRole; active?: boolean }, expected: number) => {
      const selected = scope();
      return request(base() + "/members/" + userId(target), "PATCH", value => {
        const updated = member(value, selected, target);
        need((change.role === undefined || updated.role === change.role) && (change.active === undefined || updated.active === change.active));
        return updated;
      }, change, expected);
    },
    transferOwner: (target: string, expected: number) => {
      const selected = scope();
      return request(base() + "/transfer-owner", "POST", value => {
        const updated = member(value, selected, target); need(updated.role === "owner" && updated.active); return updated;
      }, { user_id: userId(target) }, expected);
    },
    invite: (target: string, grantedRole: InviteRole, expiresInSeconds = 86400) => {
      const selected = scope();
      return request(base() + "/invitations", "POST", value => {
        const x = inWorkspace(value, selected);
        need(identifier(x.invitation_id) && x.user_id === target && x.role === grantedRole && role(x.role)
          && x.role !== "owner" && instant(x.expires_at) && text(x.token, 128) && x.token.length >= 43);
        return { workspace_id: selected, invitation_id: x.invitation_id, user_id: target,
          role: x.role, expires_at: x.expires_at, token: x.token } as WorkspaceInvitation;
      }, { user_id: userId(target), role: grantedRole, expires_in_seconds: expiresInSeconds }, undefined, 201);
    },
    revokeInvitation: (invitationId: string) => {
      const selected = scope();
      return request(base() + "/invitations/" + userId(invitationId), "DELETE", value => {
        const x = inWorkspace(value, selected);
        need(x.invitation_id === invitationId && x.revoked === true);
        return { workspace_id: selected, invitation_id: invitationId, revoked: true as const };
      }, {});
    },
    setSourceGrant: (sourceId: string, target: string, actions: GrantAction[], expected: number) => {
      const selected = scope();
      return request(base() + "/sources/" + userId(sourceId) + "/grants/" + userId(target), "PUT", value => {
        const x = inWorkspace(value, selected);
        const allowed = ["read", "write", "review", "export", "delete"];
        need(x.source_id === sourceId && x.user_id === target && revision(x.revision)
          && Array.isArray(x.actions) && x.actions.length <= 5 && x.actions.every(action => typeof action === "string" && allowed.includes(action))
          && new Set(x.actions).size === x.actions.length && (!x.actions.length || x.actions.includes("read"))
          && x.actions.length === actions.length && actions.every(action => (x.actions as unknown[]).includes(action)));
        return { workspace_id: selected, source_id: sourceId, user_id: target,
          actions: x.actions, revision: x.revision } as WorkspaceSourceGrant;
      }, { actions }, expected);
    },
    sourceGrant: (sourceId: string, target: string) => {
      const selected = scope();
      return request(base() + "/sources/" + userId(sourceId) + "/grants/" + userId(target), "GET", value => {
        const x = inWorkspace(value, selected), allowed = ["read", "write", "review", "export", "delete"];
        need(x.source_id === sourceId && x.user_id === target && revision(x.revision) && Array.isArray(x.actions)
          && x.actions.length <= 5 && x.actions.every(action => typeof action === "string" && allowed.includes(action))
          && new Set(x.actions).size === x.actions.length && (!x.actions.length || x.actions.includes("read")));
        return { workspace_id: selected, source_id: sourceId, user_id: target, actions: x.actions, revision: x.revision } as WorkspaceSourceGrant;
      });
    },
  };
}
