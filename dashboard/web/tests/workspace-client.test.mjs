import assert from "node:assert/strict";
import { test } from "node:test";
import { createWorkspaceClient, WorkspaceRequestDiscarded } from "../src/service/workspace-client.ts";

const VERSION = "product-api/v1-draft";
const session = () => ({ user_id: "actor1", display_name: "Synthetic", email: "actor@example.invalid", email_verified: true,
  context_id: "ctx1", expires_at: "2026-11-11T12:00:00Z", csrf_token: "synthetic-csrf-token", onboarding: "ready", last_workspace_id: "ws1" });
const member = { schema_version: VERSION, workspace_id: "ws1", user_id: "target1", display_name: "Invited Synthetic", role: "viewer", active: true, revision: 3 };
const workspace = { schema_version: VERSION, workspace_id: "ws1", workspace_name: "Synthetic", membership: { role: "owner", revision: 1 } };
const token = "synthetic-invitation-" + "i".repeat(43);
const invitation = { schema_version: VERSION, workspace_id: "ws1", invitation_id: "inv1", user_id: "target1", role: "viewer",
  expires_at: "2026-11-11T12:00:00Z", token };
const problem = (code, status) => ({ schema_version: VERSION, type: "urn:aletheia:problem:" + code.replaceAll("_", "-"),
  title: "Request could not be completed", status, code, detail: "Safe service detail.", request_id: "req1" });
const reply = (body, status = 200) => ({ status, json: async () => body });

function harness(body, status = 200, extra = {}) {
  const calls = [], errors = [];
  return { calls, errors, client: createWorkspaceClient({ session: session(), workspaceId: "ws1", isCurrent: () => true,
    fetch: async (url, init) => { calls.push({ url, init }); return reply(body, status); },
    onAuthError: error => errors.push(error.code), ...extra }) };
}

test("workspace API sends all eight operations to the server with CSRF and revision where required", async () => {
  const scenarios = [
    [workspace, 201, c => c.create("Synthetic"), "/v1/workspaces", "POST", { name: "Synthetic" }],
    [workspace, 200, c => c.acceptInvitation(token), "/v1/invitations/accept", "POST", { token }],
    [{ schema_version: VERSION, workspace_id: "ws1", items: [member], next_cursor: null }, 200, c => c.members(), "/v1/workspaces/ws1/members?limit=50", "GET"],
    [member, 200, c => c.changeMember("target1", { role: "viewer" }, 2), "/v1/workspaces/ws1/members/target1", "PATCH", { role: "viewer" }, '"2"'],
    [{ ...member, role: "owner" }, 200, c => c.transferOwner("target1", 2), "/v1/workspaces/ws1/transfer-owner", "POST", { user_id: "target1" }, '"2"'],
    [invitation, 201, c => c.invite("target1", "viewer", 600), "/v1/workspaces/ws1/invitations", "POST", { user_id: "target1", role: "viewer", expires_in_seconds: 600 }],
    [{ schema_version: VERSION, workspace_id: "ws1", invitation_id: "inv1", revoked: true }, 200, c => c.revokeInvitation("inv1"), "/v1/workspaces/ws1/invitations/inv1", "DELETE", {}],
    [{ schema_version: VERSION, workspace_id: "ws1", source_id: "src1", user_id: "target1", actions: ["read"], revision: 1 }, 200,
      c => c.setSourceGrant("src1", "target1", ["read"], 0), "/v1/workspaces/ws1/sources/src1/grants/target1", "PUT", { actions: ["read"] }, '"0"'],
  ];
  for (const [value, status, operation, url, method, body, revision] of scenarios) {
    const h = harness(value, status);
    await operation(h.client);
    assert.equal(h.calls.length, 1);
    const request = h.calls[0];
    assert.equal(request.url, url); assert.equal(request.init.method, method);
    assert.equal(request.init.credentials, "same-origin"); assert.equal(request.init.cache, "no-store");
    assert.equal(request.init.headers["If-Match"], revision);
    assert.equal(request.init.headers["X-CSRF-Token"], method === "GET" ? undefined : "synthetic-csrf-token");
    assert.equal(request.init.headers.Cookie, undefined);
    if (body) assert.deepEqual(JSON.parse(request.init.body), body);
    assert.equal(request.init.body?.includes("actor1"), method === "GET" ? undefined : false);
  }
});

test("members use opaque encoded cursor and refuse wrong workspace or child scope", async () => {
  const h = harness({ schema_version: VERSION, workspace_id: "ws1", items: [member], next_cursor: "opaque-next" });
  assert.equal((await h.client.members({ limit: 20, cursor: "opaque.a+b/c=" })).next_cursor, "opaque-next");
  const url = new URL(h.calls[0].url, "https://example.invalid");
  assert.equal(url.searchParams.get("limit"), "20"); assert.equal(url.searchParams.get("cursor"), "opaque.a+b/c=");
  for (const body of [
    { schema_version: VERSION, workspace_id: "ws2", items: [], next_cursor: null },
    { schema_version: VERSION, workspace_id: "ws1", items: [{ ...member, workspace_id: "ws2" }], next_cursor: null },
    { schema_version: VERSION, workspace_id: "ws1", items: [member, member], next_cursor: null },
    { schema_version: VERSION, workspace_id: "ws1", items: [], next_cursor: "" },
    { schema_version: VERSION, workspace_id: "ws1", items: [] },
  ]) await assert.rejects(harness(body).client.members(), { code: "response_invalid" });
});

test("targeted mutation responses cannot substitute another member/source/invitation", async () => {
  await assert.rejects(harness({ ...member, user_id: "other" }).client.changeMember("target1", { active: false }, 2), { code: "response_invalid" });
  await assert.rejects(harness({ ...member, workspace_id: "ws2" }).client.transferOwner("target1", 2), { code: "response_invalid" });
  await assert.rejects(harness(member).client.transferOwner("target1", 2), { code: "response_invalid" });
  await assert.rejects(harness(member).client.changeMember("target1", { active: false }, 2), { code: "response_invalid" });
  await assert.rejects(harness({ ...invitation, user_id: "other" }, 201).client.invite("target1", "viewer"), { code: "response_invalid" });
  await assert.rejects(harness({ ...invitation, expires_at: "2026-02-30T12:00:00Z" }, 201).client.invite("target1", "viewer"), { code: "response_invalid" });
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws1", invitation_id: "other", revoked: true }).client.revokeInvitation("inv1"), { code: "response_invalid" });
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws1", source_id: "other", user_id: "target1", actions: ["read"], revision: 1 })
    .client.setSourceGrant("src1", "target1", ["read"], 0), { code: "response_invalid" });
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws1", source_id: "src1", user_id: "target1", actions: ["read", ["delete"]], revision: 1 })
    .client.setSourceGrant("src1", "target1", ["read"], 0), { code: "response_invalid" });
});

test("invitation and CAS codes preserve exact status and never automatically retry", async () => {
  for (const [code, status] of [["invitation_expired", 410], ["invitation_unavailable", 404], ["invitation_wrong_account", 403], ["revision_conflict", 412]]) {
    const h = harness(problem(code, status), status);
    await assert.rejects(h.client.acceptInvitation(token), error => error.code === code && !error.safe_message.includes("Safe service detail"));
    assert.equal(h.calls.length, 1); assert.deepEqual(h.errors, []);
    await assert.rejects(harness(problem(code, status), 500).client.acceptInvitation(token), { code: "response_invalid" });
  }
});

test("only confirmed auth Problems reach the host guard", async () => {
  const h = harness(problem("membership_revoked", 403), 403);
  await assert.rejects(h.client.members(), { code: "membership_revoked" }); assert.deepEqual(h.errors, ["membership_revoked"]);
  const mismatch = harness(problem("membership_revoked", 403), 401);
  await assert.rejects(mismatch.client.members(), { code: "response_invalid" }); assert.deepEqual(mismatch.errors, []);
});

test("scope changes after headers or during JSON discard success and auth errors", async () => {
  for (const duringBody of [false, true]) {
    let current = true, release;
    const wait = new Promise(resolve => { release = resolve; });
    const h = harness(null, 200, { isCurrent: () => current, fetch: async () => {
      if (!duringBody) await wait;
      return { status: 403, json: async () => { if (duringBody) await wait; return problem("membership_revoked", 403); } };
    } });
    const pending = h.client.members();
    await Promise.resolve(); current = false; release();
    await assert.rejects(pending, WorkspaceRequestDiscarded); assert.deepEqual(h.errors, []);
  }
});

test("abort before sending and abandoned fetch do not report an auth failure", async () => {
  const controller = new AbortController(); controller.abort();
  const h = harness(workspace, 201, { signal: controller.signal });
  await assert.rejects(h.client.create("Synthetic"), WorkspaceRequestDiscarded); assert.equal(h.calls.length, 0);
  const aborted = harness(null, 200, { fetch: async () => { throw new DOMException("Aborted", "AbortError"); } });
  await assert.rejects(aborted.client.members(), WorkspaceRequestDiscarded); assert.deepEqual(aborted.errors, []);
});

test("lost mutation response stays unknown and is sent once", async () => {
  let sent = 0;
  const h = harness(null, 200, { fetch: async () => { sent++; throw new Error("synthetic transport failure"); } });
  await assert.rejects(h.client.changeMember("target1", { active: false }, 2), { code: "outcome_unknown" });
  assert.equal(sent, 1);
});

test("workspace chooser lists opaque pages and reads the exact workspace selected", async () => {
  const h = harness({ schema_version: VERSION, items: [workspace], next_cursor: "workspace-next", total: null, total_kind: "unknown" });
  const page = await h.client.workspaces({ limit: 10, cursor: "signed.cursor+opaque=" });
  assert.equal(page.items[0].workspace_name, "Synthetic"); assert.equal(page.next_cursor, "workspace-next");
  const url = new URL(h.calls[0].url, "https://example.invalid");
  assert.equal(url.pathname, "/v1/workspaces"); assert.equal(url.searchParams.get("cursor"), "signed.cursor+opaque=");
  const other = { ...workspace, workspace_id: "ws2", workspace_name: "Other authorized workspace" };
  const selected = harness(other);
  assert.equal((await selected.client.readWorkspace("ws2")).workspace_id, "ws2");
  assert.equal(selected.calls[0].url, "/v1/workspaces/ws2");
  await assert.rejects(harness(other).client.readWorkspace("ws1"), { code: "response_invalid" });
  await assert.rejects(harness({ schema_version: VERSION, items: [workspace, workspace], next_cursor: null }).client.workspaces(), { code: "response_invalid" });
});

test("member names remain server facts and are never inferred from account IDs", async () => {
  const named = harness({ schema_version: VERSION, workspace_id: "ws1", items: [member], next_cursor: null });
  assert.equal((await named.client.members()).items[0].display_name, "Invited Synthetic");
  const legacy = { ...member }; delete legacy.display_name;
  assert.equal((await harness({ schema_version: VERSION, workspace_id: "ws1", items: [legacy], next_cursor: null }).client.members()).items[0].display_name, null);
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws1", items: [{ ...member, display_name: 1 }], next_cursor: null }).client.members(), { code: "response_invalid" });
});

test("pending invitation pages expose names and scope but reject token or token-hash fields", async () => {
  const pending = { schema_version: VERSION, workspace_id: "ws1", invitation_id: "inv1", user_id: "target1",
    display_name: "Invited Synthetic", role: "viewer", expires_at: "2026-11-11T12:00:00Z" };
  const body = value => ({ schema_version: VERSION, workspace_id: "ws1", items: [value], next_cursor: null, total: null, total_kind: "unknown" });
  const h = harness(body(pending));
  const page = await h.client.pendingInvitations({ limit: 20, cursor: "pending.cursor" });
  assert.equal(page.items[0].display_name, "Invited Synthetic");
  assert.equal(h.calls[0].url, "/v1/workspaces/ws1/invitations?limit=20&cursor=pending.cursor");
  assert.equal("token" in page.items[0], false);
  for (const invalid of [{ ...pending, token }, { ...pending, token_digest: "a".repeat(64) }, { ...pending, workspace_id: "ws2" }]) {
    await assert.rejects(harness(body(invalid)).client.pendingInvitations(), { code: "response_invalid" });
  }
});

test("exact email lookup is a CSRF protected POST and returns ambiguous accounts without inviting one", async () => {
  const items = [{ user_id: "account-a", display_name: "Synthetic A" }, { user_id: "account-b", display_name: "Synthetic B" }];
  const h = harness({ schema_version: VERSION, workspace_id: "ws1", items, next_cursor: "target-next", total: null, total_kind: "unknown" });
  const page = await h.client.lookupInvitationTargets("same@example.invalid", { limit: 10, cursor: "signed.lookup" });
  assert.deepEqual(page.items, items); assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, "/v1/workspaces/ws1/invitation-targets");
  assert.equal(h.calls[0].init.method, "POST"); assert.equal(h.calls[0].init.headers["X-CSRF-Token"], "synthetic-csrf-token");
  assert.deepEqual(JSON.parse(h.calls[0].init.body), { email: "same@example.invalid", limit: 10, cursor: "signed.lookup" });
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws2", items, next_cursor: null }).client.lookupInvitationTargets("same@example.invalid"), { code: "response_invalid" });
  await assert.rejects(harness({ schema_version: VERSION, workspace_id: "ws1", items: [{ user_id: "account-a" }], next_cursor: null })
    .client.lookupInvitationTargets("same@example.invalid"), { code: "response_invalid" });
});

test("lookup transport failures are read failures and never suggest an invitation was accepted", async () => {
  let calls = 0;
  const h = harness(null, 200, { fetch: async () => { calls++; throw new Error("synthetic lost lookup"); } });
  await assert.rejects(h.client.lookupInvitationTargets("same@example.invalid"), { code: "unavailable" });
  assert.equal(calls, 1);
});

test("explicit source grant reads accept empty revision-zero grants and reject a substituted target", async () => {
  const body = { schema_version: VERSION, workspace_id: "ws1", source_id: "src1", user_id: "target1", actions: [], revision: 0 };
  const h = harness(body);
  const value = await h.client.sourceGrant("src1", "target1");
  assert.deepEqual(value.actions, []); assert.equal(value.revision, 0);
  assert.equal(h.calls[0].url, "/v1/workspaces/ws1/sources/src1/grants/target1"); assert.equal(h.calls[0].init.method, "GET");
  await assert.rejects(harness({ ...body, user_id: "other" }).client.sourceGrant("src1", "target1"), { code: "response_invalid" });
});


test("a missing host fence fails closed before any request", async () => {
  let calls = 0;
  const client = createWorkspaceClient({ session: session(), workspaceId: "ws1", fetch: async () => {
    calls++; throw new Error("must not send");
  }});
  await assert.rejects(client.members(), WorkspaceRequestDiscarded);
  assert.equal(calls, 0);
});
