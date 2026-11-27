import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedBootstrap } from "../src/service/hosted-bootstrap.ts";

const VERSION = "product-api/v1-draft";
const session = (user = "actor", context = "ctx") => ({ schema_version: VERSION,
  actor: { user_id: user, display_name: "Server account", email: "synthetic@example.invalid", email_verified: true },
  session: { context_id: context, expires_at: "2030-01-01T00:00:00Z" }, csrf_token: "synthetic-csrf-token",
  onboarding: "ready", last_workspace_id: null, can_create_workspace: false });
const caps = workspace => ({ schema_version: VERSION, workspace_id: workspace, workspace_name: "Server workspace",
  deployment_mode: "hosted", membership: { role: "analyst", revision: 1 }, analysis_backends: ["local_evidence"],
  supported_source_types: ["zip_bundle"], supported_export_formats: [], allowed_actions: ["import", "analyze"], features: {},
  limits: { question_chars: 2000, upload_bundle_bytes: 1024, page_max: 50, job_poll_min_ms: 1000 } });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const problem = (code, status) => json({ schema_version: VERSION, type: "urn:problem:test", title: "Denied", status,
  code, detail: "Safe denial.", request_id: "req1" }, status);
function system(reply) {
  const calls = [], messages = []; let clears = 0, clock = 0;
  const boot = createHostedBootstrap({ fetch: async (url, init) => { calls.push({ url, init }); return reply(url, init); },
    onClear: () => { clears++; }, tell: value => messages.push(value), now: () => clock });
  return { boot, calls, messages, clears: () => clears, clock: value => { clock = value; } };
}

test("hosted startup trusts only the server session; an unauthenticated account gets no seeded workspace", async () => {
  const h = system(() => problem("session_expired", 401));
  await h.boot.start();
  assert.equal(h.boot.snapshot().status, "signed_out"); assert.equal(h.boot.snapshot().session, null);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, "/v1/session");
  assert.equal(h.calls[0].init.credentials, "same-origin");
  h.boot.stop();
});

test("a revoked session is distinct from a new session and an untrusted refusal is a startup error", async () => {
  const revoked = system(() => problem("session_revoked", 401));
  await revoked.boot.start();
  assert.equal(revoked.boot.snapshot().status, "signed_out");
  assert.equal(revoked.boot.snapshot().error.code, "session_revoked");
  revoked.boot.stop();
  const untrusted = system(() => new Response("upstream refused", { status: 401 }));
  await untrusted.boot.start();
  assert.equal(untrusted.boot.snapshot().status, "error");
  untrusted.boot.stop();
});
test("a real session precedes workspace scope; substituted capabilities never become current", async () => {
  const h = system(url => json(url === "/v1/session" ? session() : caps("another")));
  await h.boot.start();
  assert.equal(h.boot.snapshot().session.user_id, "actor"); assert.ok(h.boot.guard());
  await h.boot.selectWorkspace("chosen");
  assert.equal(h.boot.snapshot().workspace, null);
  assert.equal(h.boot.snapshot().error.code, "response_out_of_scope");
  h.boot.stop();
});

test("workspace switches clear old state before awaiting and discard an earlier success", async () => {
  const pending = new Map();
  const h = system(url => url === "/v1/session" ? json(session()) : new Promise(resolve => pending.set(url, resolve)));
  await h.boot.start();
  const first = h.boot.selectWorkspace("a"), generation = h.boot.snapshot().generation;
  const second = h.boot.selectWorkspace("b");
  assert.equal(h.boot.snapshot().workspace, null); assert.equal(h.boot.isCurrent(generation), false);
  pending.get("/v1/workspaces/b/capabilities")(json(caps("b"))); await second;
  pending.get("/v1/workspaces/a/capabilities")(json(caps("a"))); await first;
  assert.equal(h.boot.snapshot().workspace.workspace_id, "b");
  h.boot.stop();
});

test("a workspace being opened is marked until its capabilities answer, and never left marked", async () => {
  const pending = new Map();
  const h = system(url => url === "/v1/session" ? json(session()) : new Promise(resolve => pending.set(url, resolve)));
  await h.boot.start();
  assert.equal(h.boot.snapshot().opening, null);
  const opened = h.boot.selectWorkspace("a");
  assert.equal(h.boot.snapshot().opening, "a"); assert.equal(h.boot.snapshot().workspace, null);
  pending.get("/v1/workspaces/a/capabilities")(json(caps("a"))); await opened;
  assert.equal(h.boot.snapshot().opening, null); assert.equal(h.boot.snapshot().workspace.workspace_id, "a");
  // refused: the mark goes with the refusal, so the workspace list can be shown again
  const refused = h.boot.selectWorkspace("b");
  pending.get("/v1/workspaces/b/capabilities")(problem("permission_denied", 403)); await refused;
  assert.equal(h.boot.snapshot().opening, null); assert.equal(h.boot.snapshot().error.code, "permission_denied");
  // the session is revoked while waiting: nothing stays marked, and the late answer opens nothing
  const abandoned = h.boot.selectWorkspace("c");
  assert.equal(h.boot.snapshot().opening, "c");
  h.boot.authError({ code: "session_revoked" });
  assert.equal(h.boot.snapshot().status, "signed_out");
  pending.get("/v1/workspaces/c/capabilities")(json(caps("c"))); await abandoned;
  assert.equal(h.boot.snapshot().opening, null); assert.equal(h.boot.snapshot().workspace, null);
  h.boot.stop();
});

test("session refreshes deduplicate and another account cannot inherit private state", async () => {
  let answer = session(), delayed;
  const h = system(() => delayed ? new Promise(resolve => { delayed = resolve; }) : json(answer));
  await h.boot.start();
  delayed = true;
  const first = h.boot.refreshSession(), second = h.boot.refreshSession();
  assert.equal(h.calls.length, 2);
  delayed(json(session("other", "other-ctx")));
  await Promise.allSettled([first, second]);
  assert.equal(h.boot.snapshot().status, "signed_out"); assert.equal(h.boot.snapshot().session, null);
  assert.ok(h.clears() >= 2);
  h.boot.stop();
});

for (const scenario of ["renewed", "renew denied", "fresh read denied"]) {
  test(`a read crossing cookie rotation checks the current session once (${scenario})`, async () => {
    const renewal = Promise.withResolvers();
    const expired = () => problem("session_revoked", 401);
    let sessionReads = 0;
    const h = system(url => {
      if (url === "/v1/auth/renew") return renewal.promise;
      sessionReads++;
      if (sessionReads === 2 || (sessionReads === 3 && scenario === "fresh read denied")) return expired();
      return json(session());
    });
    await h.boot.start();
    const accepted = Promise.allSettled([h.boot.renew(), h.boot.renew()]);
    const checked = h.boot.guard().check(true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.boot.snapshot().status, "ready");
    renewal.resolve(scenario === "renew denied" ? expired() : json(session()));
    await accepted;
    await checked;
    assert.equal(h.calls.filter(call => call.url === "/v1/auth/renew").length, 1);
    assert.equal(sessionReads, scenario === "renew denied" ? 2 : 3);
    assert.equal(h.boot.snapshot().status, scenario === "renewed" ? "ready" : "signed_out");
    h.boot.stop();
  });
}

test("a changed membership fences the result and reloads only current capabilities", async () => {
  let revision = 1;
  const h = system(url => json(url === "/v1/session" ? session() : { ...caps("w"), membership: { role: "analyst", revision } }));
  await h.boot.start(); await h.boot.selectWorkspace("w");
  const generation = h.boot.snapshot().generation;
  revision = 2; await h.boot.refreshCapabilities("w");
  assert.equal(h.boot.snapshot().workspace.membership_revision, 2); assert.equal(h.boot.isCurrent(generation), false);
  h.boot.stop();
});

test("visible private views cover at the hosted confirmation lease even with a check in flight", async () => {
  const h = system(() => json(session())); await h.boot.start();
  const guard = h.boot.guard();
  h.clock(3999); guard.onTick(true); assert.equal(guard.state(), "active");
  h.clock(4000); guard.onTick(true); assert.equal(guard.state(), "confirming");
  await guard.check(true); assert.equal(guard.state(), "active");
  h.boot.stop();
});

test("known revocation clears workspace/session; successful logout broadcasts no private content", async () => {
  let revoked = false;
  const h = system(url => url === "/v1/session" ? json(session()) : url === "/v1/auth/logout"
    ? json({ logout_url: null }) : revoked ? problem("session_revoked", 401) : json(caps("w")));
  await h.boot.start(); await h.boot.selectWorkspace("w"); revoked = true;
  await assert.rejects(h.boot.refreshCapabilities("w"));
  assert.equal(h.boot.snapshot().session, null); assert.equal(h.boot.snapshot().workspace, null);
  await h.boot.start(); await h.boot.logout();
  assert.equal(h.boot.snapshot().status, "signed_out");
  assert.deepEqual(h.messages.at(-1), { type: "revoked" });
  h.boot.stop();
});

test("stopping an unresolved bootstrap prevents its late session from becoming visible", async () => {
  let resolve;
  const h = system(() => new Promise(done => { resolve = done; }));
  const starting = h.boot.start(); h.boot.stop(); resolve(json(session())); await starting;
  assert.equal(h.boot.snapshot().session, null);
});

test("the hosted guard drops revoked workspace scope while retaining its valid session", async () => {
  let revoked = false;
  const h = system(url => url === "/v1/session" ? json(session())
    : revoked ? problem("membership_revoked", 403) : json(caps("w")));
  await h.boot.start(); await h.boot.selectWorkspace("w");
  const guard = h.boot.guard(); revoked = true;
  await guard.check(true);
  assert.equal(guard.state(), "active");
  assert.equal(h.boot.snapshot().session.user_id, "actor"); assert.equal(h.boot.snapshot().workspace, null);
  assert.equal(h.boot.snapshot().error.code, "membership_revoked");
  assert.equal(h.messages.some(message => message.type === "revoked"), false);
  h.boot.stop();
});

test("J02 membership loss fences late reads and allows another workspace without signing in again", async () => {
  let revoked = false, release;
  const h = system(url => url === "/v1/session" ? json(session())
    : url.endsWith("/jobs/late") ? new Promise(done => { release = done; })
    : url.includes("/w/") && revoked ? problem("membership_revoked", 403)
    : json(caps(url.includes("/other/") ? "other" : "w")));
  await h.boot.start(); await h.boot.selectWorkspace("w");
  const generation = h.boot.snapshot().generation;
  const late = h.boot.client().loadJob("w", "late");
  const discarded = assert.rejects(late, error => error.name === "HostedRequestDiscarded");
  revoked = true;
  await assert.rejects(h.boot.refreshCapabilities("w"));
  assert.equal(h.boot.isCurrent(generation), false);
  assert.equal(h.boot.snapshot().status, "ready");
  assert.equal(h.boot.snapshot().session.user_id, "actor");
  assert.equal(h.boot.snapshot().workspace, null);
  assert.equal(h.messages.some(message => message.type === "revoked"), false);
  release(json({ schema_version: VERSION, workspace_id: "w", job_id: "late", status: "succeeded", progress: null }));
  await discarded;
  await h.boot.selectWorkspace("other");
  assert.equal(h.boot.snapshot().workspace.workspace_id, "other");
  h.boot.stop();
});

test("a project job denied by the server clears private scope while retaining the authenticated account", async () => {
  const h = system(url => url === "/v1/session" ? json(session())
    : url.endsWith("/capabilities") ? json(caps("w")) : problem("resource_not_found", 404));
  await h.boot.start(); await h.boot.selectWorkspace("w");
  const generation = h.boot.snapshot().generation;
  h.boot.watchJob("private-job"); await h.boot.guard().check(true);
  assert.equal(h.boot.snapshot().session.user_id, "actor");
  assert.equal(h.boot.snapshot().workspace, null); assert.equal(h.boot.isCurrent(generation), false);
  assert.equal(h.boot.snapshot().error.code, "permission_denied");
  assert.ok(h.calls.some(call => call.url === "/v1/workspaces/w/jobs/private-job"));
  h.boot.stop();
});

test("partial session and workspace confirmations do not extend the current project's visibility lease", async () => {
  let pending;
  const h = system(url => url === "/v1/session" ? json(session())
    : url.endsWith("/capabilities") ? json(caps("w")) : new Promise(resolve => { pending = resolve; }));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchJob("private-job");
  const guard = h.boot.guard(); h.clock(3500);
  const checking = guard.check(false);
  while (!pending) await new Promise(resolve => setImmediate(resolve));
  h.clock(4000); guard.onTick(true); assert.equal(guard.state(), "confirming");
  pending(problem("resource_not_found", 404)); await checking;
  assert.equal(h.boot.snapshot().workspace, null);
  h.boot.stop();
});

test("another tab's confirmation cannot uncover this tab before its own scope check", async () => {
  let hold = false, answer;
  const h = system(() => hold ? new Promise(resolve => { answer = resolve; }) : json(session()));
  await h.boot.start(); hold = true;
  const guard = h.boot.guard(); h.clock(4000); guard.onTick(true);
  guard.onPeer({ type: "confirmed", context_id: "ctx" });
  assert.equal(guard.state(), "confirming");
  const checking = guard.check(true); answer(json(session())); await checking; assert.equal(guard.state(), "active");
  h.boot.stop();
});

/* K15/K04: the result on screen is confirmed by a read of that result, never by the session,
   capabilities or a job alone. Shapes follow the K10 header binding (GET results/{id}/header). */
const RESULT = "p6-result-" + "a".repeat(64), PROJECT = "p3-project-" + "b".repeat(64), SNAPSHOT = "p3-snapshot-" + "c".repeat(64);
const shown = { resultId: RESULT, projectId: PROJECT, snapshotId: SNAPSHOT };
const HEADER = `/v1/workspaces/w/results/${RESULT}/header`;
const header = (over = {}) => ({ schema_version: VERSION, workspace_id: "w", project_id: PROJECT, result_id: RESULT, snapshot_id: SNAPSHOT,
  view_schema_version: "p6-product-view/v1", project: { id: PROJECT, display_name: "Synthetic" },
  runtime: { provider: "local_evidence", model: "upload-snapshot-metric-observation/v2", external_call: false },
  result: { id: RESULT, status: "complete", disposition: "abstain", causal_status: "unverified", summary: "Observed.",
    denominators: { independent_families: null, contexts: 1, outputs: 1, claims: 2 },
    allowed_wording: "Observed.", forbidden_wording: "Cause.", caveat: "Synthetic." },
  conversation_id: "conversation1", turn_count: 1, claim_count: 2, evidence_count: 2, node_count: 3, edge_count: 2, ...over });
const featured = features => ({ ...caps("w"), features });
async function watching(features, answer) {
  const h = system(url => url === "/v1/session" ? json(session()) : url.endsWith("/capabilities") ? json(featured(features)) : answer(url));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchResult(shown);
  return h;
}

test("W1 the result on screen is confirmed by its own header, held to the shown result, project and snapshot", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => json(header()));
  const guard = h.boot.guard();
  // showing it asks for its own header at once (R4.11: an earlier check confirms nothing about it)
  await guard.check(false); assert.equal(guard.state(), "active");
  assert.equal(h.calls.filter(call => call.url === HEADER).length, 1);
  h.clock(4000); guard.onTick(true); assert.equal(guard.state(), "confirming");
  await guard.check(false);
  assert.equal(guard.state(), "active");
  assert.ok(h.calls.some(call => call.url === HEADER));
  h.boot.stop();
});

test("resuming cancels an old result read and rechecks only the shown scope", async () => {
  let held;
  const h = await watching({ "catalog.paged_read": true }, url => url === HEADER && !held
    ? new Promise(resolve => { held = resolve; })
    : json(header()));
  const guard = h.boot.guard();
  const waiting = guard.check(false);
  while (!held) await new Promise(resolve => setImmediate(resolve));
  const old = h.calls.find(call => call.url === HEADER);
  guard.onBackground();
  assert.equal(old.init.signal.aborted, true);
  assert.equal(guard.state(), "confirming");
  await guard.onFocused();
  held(problem("membership_revoked", 403));
  await waiting;
  assert.equal(guard.state(), "active");
  assert.equal(h.boot.snapshot().workspace.workspace_id, "w");
  assert.equal(h.calls.filter(call => call.url === HEADER).length, 2);
  assert.ok(h.calls.every(call => call.init.method === "GET"), "resuming cannot submit or recreate an export");
  h.boot.stop();
});
test("W2 a refused header (404) drops the workspace scope; the signed-in account stays", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => problem("record_not_found", 404));
  const generation = h.boot.snapshot().generation;
  await h.boot.guard().check(true);
  assert.equal(h.boot.snapshot().workspace, null); assert.equal(h.boot.isCurrent(generation), false);
  assert.equal(h.boot.snapshot().error.code, "permission_denied");
  assert.equal(h.boot.snapshot().session.user_id, "actor");
  h.boot.stop();
});

test("W3 a header about another result confirms nothing: the view stays covered", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => json(header({ snapshot_id: "p3-snapshot-" + "9".repeat(64) })));
  const guard = h.boot.guard();
  await guard.check(true);
  assert.equal(guard.state(), "unreachable");
  assert.equal(h.boot.snapshot().workspace.workspace_id, "w", "an out-of-scope answer is not a denial");
  h.boot.stop();
});

test("W4 without a result read on this server nothing is assumed: covered, and no other probe stands in", async () => {
  const h = await watching({}, () => json(header()));
  const guard = h.boot.guard();
  await guard.check(true);
  assert.equal(guard.state(), "unreachable");
  assert.equal(h.calls.some(call => call.url.includes("/results/")), false);
  h.boot.stop();
});

test("W5 a server with catalog.read but no header is never read in full to confirm: covered instead (Kiên, 13/11)", async () => {
  const h = await watching({ "catalog.read": true }, () => json({}));
  await h.boot.guard().check(true);
  assert.equal(h.boot.guard().state(), "unreachable");
  assert.equal(h.calls.some(call => call.url.includes("/results/") || call.url.includes("/output")), false);
  h.boot.stop();
});

test("W9 a job this tab is polling is confirmed by that polling, from when its read was sent; the check does not read it again", async () => {
  const h = system(url => url === "/v1/session" ? json(session()) : url.endsWith("/capabilities") ? json(caps("w"))
    : json({ schema_version: VERSION, workspace_id: "w", job_id: "private-job", status: "running", progress: null }));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchJob("private-job");
  const guard = h.boot.guard();
  h.clock(1000); await h.boot.client().loadJob("w", "private-job");
  const reads = () => h.calls.filter(call => call.url === "/v1/workspaces/w/jobs/private-job").length;
  h.clock(3000); await guard.check(false);
  assert.equal(reads(), 1, "only the polling read the job");
  assert.equal(guard.state(), "active");
  // the confirmation counts from the poll at 1000, not the check at 3000: by 5000 it has run out
  h.clock(5000); guard.onTick(true); assert.equal(guard.state(), "confirming");
  // a poll older than the lease is not used; the check reads the job itself
  h.clock(9000); await guard.check(false);
  assert.equal(reads(), 2); assert.equal(guard.state(), "active");
  h.boot.stop();
});

test("W11 a poll already used by one check is not reused: the next check reads the job itself (FL-125, real API burst)", async () => {
  const h = system(url => url === "/v1/session" ? json(session()) : url.endsWith("/capabilities") ? json(caps("w"))
    : json({ schema_version: VERSION, workspace_id: "w", job_id: "private-job", status: "running", progress: null }));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchJob("private-job");
  const guard = h.boot.guard();
  const reads = () => h.calls.filter(call => call.url === "/v1/workspaces/w/jobs/private-job").length;
  h.clock(1000); await h.boot.client().loadJob("w", "private-job");
  h.clock(3000); await guard.check(false); assert.equal(reads(), 1);
  // polling has stopped; the same poll at 1000 is still inside the lease but says nothing new
  h.clock(3250); await guard.check(false);
  assert.equal(reads(), 2, "the second check read the job instead of dating itself back to 1000 again");
  assert.equal(guard.state(), "active");
  // the confirmation now counts from 3250: the guard is not due again at once
  h.clock(3500); assert.equal(guard.due(true), false);
  h.boot.stop();
});

test("W12 a watched job read as finished is no longer read by later checks", async () => {
  const h = system(url => url === "/v1/session" ? json(session()) : url.endsWith("/capabilities") ? json(caps("w"))
    : json({ schema_version: VERSION, workspace_id: "w", job_id: "private-job", status: "failed", progress: null }));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchJob("private-job");
  const guard = h.boot.guard();
  const reads = () => h.calls.filter(call => call.url === "/v1/workspaces/w/jobs/private-job").length;
  h.clock(1000); await guard.check(false); assert.equal(reads(), 1);
  h.clock(5000); await guard.check(false); h.clock(9000); await guard.check(false);
  assert.equal(reads(), 1, "a finished job cannot change, so it is not read on every check");
  assert.equal(guard.state(), "active");
  h.boot.stop();
});

test("W10 another job's read, or a refused read, never stands in for the watched job", async () => {
  let deny = false;
  const h = system(url => url === "/v1/session" ? json(session()) : url.endsWith("/capabilities") ? json(caps("w"))
    : deny ? problem("record_not_found", 404) : json({ schema_version: VERSION, workspace_id: "w", job_id: url.split("/").pop(), status: "running", progress: null }));
  await h.boot.start(); await h.boot.selectWorkspace("w"); h.boot.watchJob("private-job");
  await h.boot.client().loadJob("w", "other-job");
  deny = true; await assert.rejects(h.boot.client().loadJob("w", "private-job"));
  await h.boot.guard().check(true);
  assert.equal(h.calls.filter(call => call.url === "/v1/workspaces/w/jobs/private-job").length, 2, "the check read the job itself");
  assert.equal(h.boot.snapshot().workspace, null, "and its refusal dropped the scope");
  h.boot.stop();
});

test("W6 while a job is awaited beside an older result, both scopes are read and both must hold", async () => {
  const h = await watching({ "catalog.paged_read": true }, url => url.endsWith("/jobs/private-job")
    ? problem("record_not_found", 404) : json(header()));
  h.boot.watchJob("private-job");
  await h.boot.guard().check(true);
  assert.ok(h.calls.some(call => call.url === HEADER) && h.calls.some(call => call.url === "/v1/workspaces/w/jobs/private-job"));
  assert.equal(h.boot.snapshot().workspace, null, "the job's denial drops the scope even though the result's header answered");
  assert.equal(h.boot.guard().state(), "active", "the account itself is still confirmed");
  h.boot.stop();
});

test("W7 a header that answers after the lease it would grant confirms nothing current", async () => {
  let pending;
  const h = await watching({ "catalog.paged_read": true }, () => new Promise(resolve => { pending = resolve; }));
  const guard = h.boot.guard(); h.clock(3000);
  const checking = guard.check(false);
  while (!pending) await new Promise(resolve => setImmediate(resolve));
  h.clock(7001); pending(json(header())); await checking;
  assert.equal(guard.state(), "confirming", "counted from when the check was sent, not when it answered");
  h.boot.stop();
});

test("W8 no longer watching a result (after a delete) stops its reads", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => problem("record_not_found", 404));
  h.boot.watchResult(null);
  await h.boot.guard().check(true);
  assert.equal(h.calls.some(call => call.url === HEADER), false);
  assert.equal(h.boot.snapshot().workspace.workspace_id, "w");
  h.boot.stop();
});

/* J01.04: fanout. One check is one read of the scope on screen; session and capabilities are read in full at most every 30 s. */
const count = (h, from) => {
  const by = {}; for (const call of h.calls.slice(from)) { const key = call.url === "/v1/session" ? "session" : call.url.endsWith("/capabilities") ? "capabilities" : call.url.endsWith("/header") ? "header" : "other"; by[key] = (by[key] ?? 0) + 1; }
  return by;
};

test("W13 with a result on screen a check is one header read; session and capabilities are read again only every 30 s", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => json(header()));
  const guard = h.boot.guard();
  await guard.check(false); // the first check after start reads everything once
  const from = h.calls.length;
  // 30 s of checks every 3 s: ten checks
  for (let t = 3_000; t < 30_000; t += 3_000) { h.clock(t); await guard.check(false); assert.equal(guard.state(), "active"); }
  assert.deepEqual(count(h, from), { header: 9 });
  h.clock(30_000); await guard.check(false);
  assert.deepEqual(count(h, from), { header: 10, session: 1, capabilities: 1 });
  h.boot.stop();
});

test("W14 the one read still sees a change within one check: a lost membership drops the workspace, a revoked or expired session ends access", async () => {
  for (const [code, status, expect] of [["membership_revoked", 403, "active"], ["session_revoked", 401, "revoked"], ["session_expired", 401, "expired"]]) {
    let refuse = false;
    const h = await watching({ "catalog.paged_read": true }, () => refuse ? problem(code, status) : json(header()));
    const guard = h.boot.guard();
    await guard.check(false); h.clock(3_000); await guard.check(false);
    refuse = true; const from = h.calls.length;
    h.clock(6_000); await guard.check(false);
    assert.equal(guard.state(), expect, code);
    assert.deepEqual(count(h, from), { header: 1 }, `${code}: no session or capabilities read was needed to see it`);
    if (expect !== "expired") assert.equal(h.boot.snapshot().workspace, null, `${code}: nothing of the scope stays`);
    if (code === "membership_revoked") {
      assert.equal(h.boot.snapshot().session.user_id, "actor", "the session itself is kept (J02)");
      assert.equal(h.boot.snapshot().error.code, "membership_revoked");
      assert.equal(h.messages.some(message => message.type === "revoked"), false, "other tabs are not signed out");
    }
    h.boot.stop();
  }
});

test("W15 with only the workspace open, a check reads its capabilities alone; a lost membership there drops the workspace", async () => {
  let revoked = false;
  const h = system(url => url === "/v1/session" ? json(session()) : revoked ? problem("membership_revoked", 403) : json(caps("w")));
  await h.boot.start(); await h.boot.selectWorkspace("w");
  const guard = h.boot.guard();
  await guard.check(false);
  const from = h.calls.length;
  h.clock(3_000); await guard.check(false); h.clock(6_000); await guard.check(false);
  assert.deepEqual(count(h, from), { capabilities: 2 });
  revoked = true; const before = h.calls.length; h.clock(9_000); await guard.check(false);
  assert.deepEqual(count(h, before), { capabilities: 1 }, "seen by that one read");
  assert.equal(guard.state(), "active", "the session is kept (J02)");
  assert.equal(h.boot.snapshot().workspace, null);
  assert.equal(h.boot.snapshot().session.user_id, "actor");
  h.boot.stop();
});

test("W16 a tab coming back after 30 s or more reads session and capabilities again before anything is shown", async () => {
  const h = await watching({ "catalog.paged_read": true }, () => json(header()));
  const guard = h.boot.guard();
  await guard.check(false);
  const from = h.calls.length;
  h.clock(45_000); await guard.onVisible(45_000);
  assert.deepEqual(count(h, from), { session: 1, capabilities: 1, header: 1 });
  assert.equal(guard.state(), "active");
  h.boot.stop();
});
