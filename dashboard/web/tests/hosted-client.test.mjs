import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedClient, HostedRequestDiscarded } from "../src/service/hosted-client.ts";

const VERSION = "product-api/v1-draft", SHA = "a".repeat(64), SNAPSHOT = "upload-snapshot-" + SHA;
const KEY = "synthetic_request_1234";
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = { workspace_id: "ws1", allowed_actions: ["import", "analyze"], upload_bundle_bytes: 1024,
  question_chars: 2000, job_poll_min_ms: 10 };
const sessionWire = { schema_version: VERSION,
  actor: { user_id: "actor1", display_name: "Synthetic", email: "user@example.invalid", email_verified: true },
  session: { context_id: "ctx1", expires_at: "2026-11-11T12:00:00Z" },
  csrf_token: "synthetic-csrf-token", onboarding: "ready", last_workspace_id: "ws1" };
const capsWire = { schema_version: VERSION, workspace_id: "ws1", workspace_name: "Synthetic",
  membership: { role: "analyst", revision: 1 }, deployment_mode: "hosted", analysis_backends: ["local_evidence"],
  supported_source_types: ["zip_bundle"], supported_export_formats: [], allowed_actions: caps.allowed_actions, features: {},
  limits: { question_chars: 2000, upload_bundle_bytes: 1024, page_max: 200, job_poll_min_ms: 10 } };
const receipt = { schema_version: VERSION, workspace_id: "ws1", job_id: "job1", status: "queued", request_id: "req1",
  status_url: "/v1/workspaces/ws1/jobs/job1" };
const job = (status = "succeeded") => ({ schema_version: VERSION, workspace_id: "ws1", job_id: "job1", status, progress: null });
const candidates = { targets: [], metric_sources: [], configs: [], runs: [] };
const preview = { source_sha256: SHA, source_state_sha256: "b".repeat(64), mapping_candidates: candidates };
const output = (operation, result) => ({ schema_version: VERSION, workspace_id: "ws1", job_id: "job1", operation, result });
const view = { schema_version: "p6-product-view/v1", demo_only: false, mode: "project_audit", visibility: "diagnosis",
  project: { id: "project1", display_name: "Synthetic" }, runtime: { provider: "local_evidence", model: "synthetic/v1", external_call: false },
  snapshot: { id: SNAPSHOT, baseline_id: SNAPSHOT, sha256: SHA, metric_definitions: [], metric_changes: [] },
  result: { id: "result1", status: "complete", causal_status: "unverified", disposition: "abstain", summary: "Observed.",
    allowed_wording: "Observed.", forbidden_wording: "Cause.", caveat: "Initial snapshot.",
    denominators: { independent_families: null, contexts: 1, outputs: 1, claims: 0 } },
  conversation: { id: "conversation1", turns: [] }, evidence: [], claims: [], graph: { nodes: [], edges: [] } };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const problem = (code, status) => ({ schema_version: VERSION, type: "urn:aletheia:problem:test", title: "Request failed",
  status, code, detail: "Safe test detail.", request_id: "req1" });
function harness(reply, options = {}) {
  const calls = [], answers = [];
  const client = createHostedClient({ session, isCurrent: () => true,
    fetch: async (url, init) => { calls.push({ url, init }); return typeof reply === "function" ? reply(url, init) : json(reply); },
    onAnswer: (status, code) => answers.push([status, code]), ...options });
  return { client, calls, answers };
}

test("bootstrap and capabilities use checked readers over fixed same-origin requests", async () => {
  const h = harness((url) => json(url === "/v1/session" ? sessionWire : capsWire));
  assert.equal((await h.client.loadSession()).context_id, "ctx1");
  assert.equal((await h.client.loadCaps("ws1")).membership_revision, 1);
  assert.equal(h.client.loginUrl(), "/v1/auth/start", "the server chooses its approved default return path");
  assert.deepEqual(h.calls.map(x => x.url), ["/v1/session", "/v1/workspaces/ws1/capabilities"]);
  for (const { init } of h.calls) {
    assert.equal(init.credentials, "same-origin"); assert.equal(init.redirect, "error"); assert.equal(init.cache, "no-store");
    assert.equal(init.headers.Cookie, undefined); assert.equal(init.headers.Authorization, undefined);
    assert.equal(init.headers["X-CSRF-Token"], undefined);
  }
  for (const [answer, code] of [[{ ...capsWire, workspace_id: "ws2" }, "response_out_of_scope"],
    [{ ...capsWire, schema_version: "product-api/v99" }, "schema_unsupported"]]) {
    await assert.rejects(harness(answer).client.loadCaps("ws1"), { code });
  }
  assert.throws(() => createHostedClient({ fetch: async () => json(null) }), { code: "invalid_request" });
});

test("upload and all three submissions send CSRF and caller-owned request keys exactly once", async () => {
  const source = { schema_version: VERSION, workspace_id: "ws1", source_id: "src1", sha256: SHA };
  const h = harness((url) => json(url.endsWith("/sources") ? source : receipt, url.endsWith("/sources") ? 201 : 202));
  assert.equal((await h.client.uploadZip("ws1", caps, new Blob(["zip"]), KEY)).source_id, "src1");
  const submissions = [
    { operation: "preview", source_id: "src1" },
    { operation: "confirm", source_id: "src1", mapping: { target: {} } },
    { operation: "analyze", snapshot_id: SNAPSHOT, question: "What is observed?" },
  ];
  for (const submission of submissions) assert.equal((await h.client.submitJob("ws1", caps, submission, KEY)).job_id, "job1");
  assert.equal(h.calls.length, 4);
  assert.equal(h.calls[0].init.headers["Content-Type"], "application/zip");
  assert.equal(h.calls[0].init.body.size, 3);
  for (const { init } of h.calls) {
    assert.equal(init.method, "POST"); assert.equal(init.headers["X-CSRF-Token"], session.csrf_token);
    assert.equal(init.headers["Idempotency-Key"], KEY);
  }
  assert.deepEqual(h.calls.slice(1).map(x => JSON.parse(x.init.body)), submissions);
});

test("capability limits, key and request shape are checked before any wire effect", () => {
  const h = harness(receipt);
  assert.throws(() => h.client.uploadZip("ws1", caps, new Blob(["x".repeat(1025)]), KEY), { code: "invalid_request" });
  assert.throws(() => h.client.uploadZip("ws1", caps, new Blob(["zip"]), "bad"), { code: "invalid_request" });
  assert.throws(() => h.client.submitJob("ws1", { ...caps, workspace_id: "ws2" }, { operation: "preview", source_id: "src1" }, KEY), { code: "invalid_request" });
  assert.throws(() => h.client.submitJob("ws1", caps, { operation: "preview", source_id: "src1", question: "extra" }, KEY), { code: "invalid_request" });
  assert.throws(() => h.client.submitJob("ws1", caps, { operation: "analyze", snapshot_id: SNAPSHOT, question: "x".repeat(2001) }, KEY), { code: "invalid_request" });
  assert.throws(() => h.client.loadJob("https://other.invalid", "job1"), { code: "invalid_request" });
  assert.equal(h.calls.length, 0);
});

test("outputs check operation, workspace, job and actual preview/confirm/view shapes", async () => {
  const cases = [["preview", preview, { operation: "preview", sourceSha256: SHA }],
    ["confirm", { snapshot_id: SNAPSHOT }, { operation: "confirm" }],
    ["analyze", view, { operation: "analyze", snapshotId: SNAPSHOT, projectId: "project1" }]];
  for (const [operation, result, expected] of cases) {
    const h = harness(output(operation, result));
    assert.deepEqual((await h.client.loadOutput("ws1", "job1", expected)).result, result);
    assert.equal(h.calls[0].url, "/v1/workspaces/ws1/jobs/job1/output");
    for (const changed of [{ workspace_id: "ws2" }, { job_id: "job2" }, { operation: "other" }]) {
      await assert.rejects(harness({ ...output(operation, result), ...changed }).client.loadOutput("ws1", "job1", expected), { code: "response_out_of_scope" });
    }
  }
  await assert.rejects(harness(output("preview", { ...preview, mapping_candidates: { ...candidates, runs: [{}] } }))
    .client.loadOutput("ws1", "job1", { operation: "preview", sourceSha256: SHA }), { code: "response_invalid" });
  await assert.rejects(harness(output("preview", preview)).client.loadOutput("ws1", "job1", { operation: "preview", sourceSha256: "b".repeat(64) }), { code: "response_invalid" });
  await assert.rejects(harness(output("confirm", { snapshot_id: "other" })).client.loadOutput("ws1", "job1", { operation: "confirm" }), { code: "response_invalid" });
  await assert.rejects(harness(output("analyze", { ...view, demo_only: true })).client.loadOutput("ws1", "job1", { operation: "analyze", snapshotId: SNAPSHOT }), { code: "response_out_of_scope" });
  await assert.rejects(harness(output("analyze", { ...view, schema_version: "unknown" })).client.loadOutput("ws1", "job1", { operation: "analyze", snapshotId: SNAPSHOT }), { code: "schema_unsupported" });
  await assert.rejects(harness({ ...job(), job_id: "job2" }).client.loadJob("ws1", "job1"), { code: "response_out_of_scope" });
});

test("trusted error codes alone reach the session guard", async () => {
  const h = harness(() => json(problem("session_revoked", 401), 401));
  await assert.rejects(h.client.loadJob("ws1", "job1"), { code: "session_revoked" });
  assert.deepEqual(h.answers, [[401, "session_revoked"]]);
  const wrong = harness(() => json(problem("session_revoked", 403), 403));
  await assert.rejects(wrong.client.loadJob("ws1", "job1"), { code: "response_invalid" });
  assert.deepEqual(wrong.answers, [[403, null]]);
});

test("transport loss and truncated successful mutations remain unknown and are never retried", async () => {
  for (const reply of [() => { throw new Error("private synthetic detail"); }, () => new Response('{"job_id":', {
    status: 202, headers: { "Content-Type": "application/json" } }), () => new Response(new ReadableStream({
      start(controller) { controller.error(new Error("private synthetic detail")); },
    }), { status: 202, headers: { "Content-Type": "application/json" } })]) {
    const h = harness(reply);
    await assert.rejects(h.client.submitJob("ws1", caps, { operation: "preview", source_id: "src1" }, KEY), error =>
      error.code === "outcome_unknown" && !error.message.includes("private synthetic detail"));
    assert.equal(h.calls.length, 1);
  }
  await assert.rejects(harness(() => new Response('{"job_id":', { headers: { "Content-Type": "application/json" } }))
    .client.loadJob("ws1", "job1"), { code: "response_invalid" });
  const moved = harness({ ...receipt, status_url: "https://other.invalid/steal" });
  await assert.rejects(moved.client.submitJob("ws1", caps, { operation: "preview", source_id: "src1" }, KEY), { code: "outcome_unknown" });
  assert.equal(moved.calls.length, 1);
  const redirected = json(job()); Object.defineProperty(redirected, "redirected", { value: true });
  await assert.rejects(harness(() => redirected).client.loadJob("ws1", "job1"), { code: "response_invalid" });
});

test("stream byte limit cancels excess data and deadline bounds a stalled body", async () => {
  let cancelled = false;
  const tooLarge = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(40)); controller.enqueue(new Uint8Array(40)); },
    cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/json" } });
  const h = harness(() => tooLarge, { maximumResponseBytes: 64 });
  await assert.rejects(h.client.loadJob("ws1", "job1"), { code: "response_invalid" });
  assert.equal(cancelled, true); assert.deepEqual(h.answers, []);
  const stalled = harness(() => new Response(new ReadableStream({}), { headers: { "Content-Type": "application/json" } }), { timeoutMs: 5 });
  await assert.rejects(stalled.client.loadJob("ws1", "job1"), { code: "deadline_exceeded" });
});

test("elapsed deadlines hold when synchronous work delays the timeout callback", async () => {
  for (const mutation of [false, true]) {
    const h = harness(() => {
      const until = performance.now() + 20;
      while (performance.now() < until) { /* Keep the timeout callback queued. */ }
      return json(mutation ? receipt : job(), mutation ? 202 : 200);
    }, { timeoutMs: 5 });
    await assert.rejects(mutation
      ? h.client.submitJob("ws1", caps, { operation: "preview", source_id: "src1" }, KEY)
      : h.client.pollJob("ws1", "job1", caps, { timeoutMs: 5 }),
    { code: mutation ? "outcome_unknown" : "deadline_exceeded" });
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.answers, []);
  }
});

test("abort and late contexts discard answers before the session guard or caller can apply them", async () => {
  let resolve, current = true;
  const h = harness(() => new Promise(r => { resolve = r; }), { isCurrent: () => current });
  const pending = h.client.loadJob("ws1", "job1"); current = false; resolve(json(job()));
  await assert.rejects(pending, HostedRequestDiscarded); assert.deepEqual(h.answers, []);
  const controller = new AbortController();
  const aborted = harness(() => new Promise(() => {}), { signal: controller.signal });
  const waiting = aborted.client.loadJob("ws1", "job1"); controller.abort();
  await assert.rejects(waiting, HostedRequestDiscarded); assert.deepEqual(aborted.answers, []);
  assert.throws(() => aborted.client.loadJob("ws1", "job1"), HostedRequestDiscarded);
  assert.equal(aborted.calls.length, 1);
  let mayApply = true;
  const guard = harness(job(), { isCurrent: () => mayApply, onAnswer: () => { mayApply = false; } });
  await assert.rejects(guard.client.loadJob("ws1", "job1"), HostedRequestDiscarded);
});

test("polling is serial, deduplicated, capability paced, bounded and cancellable", async () => {
  const times = []; let count = 0, active = 0, peak = 0;
  const h = harness(async () => {
    times.push(Date.now()); active++; peak = Math.max(active, peak);
    await new Promise(resolve => setTimeout(resolve, 1)); active--;
    return json(job(++count === 2 ? "succeeded" : "running"));
  });
  const pending = h.client.pollJob("ws1", "job1", { ...caps, job_poll_min_ms: 15 }, { timeoutMs: 100 });
  await assert.rejects(h.client.pollJob("ws1", "job1", caps), { code: "invalid_request" });
  assert.equal((await pending).status, "succeeded"); assert.equal(peak, 1); assert.ok(times[1] - times[0] >= 15);
  const bounded = harness(job("running"));
  await assert.rejects(bounded.client.pollJob("ws1", "job1", { ...caps, job_poll_min_ms: 2000 }, { timeoutMs: 5 }), { code: "deadline_exceeded" });
  assert.equal(bounded.calls.length, 1);
  const controller = new AbortController(), stopped = harness(job("running"));
  const watch = stopped.client.pollJob("ws1", "job1", { ...caps, job_poll_min_ms: 2000 }, { signal: controller.signal });
  await new Promise(resolve => setTimeout(resolve, 2)); controller.abort();
  await assert.rejects(watch, HostedRequestDiscarded); assert.equal(stopped.calls.length, 1);
});

test("logout keeps null/204 local-only and only returns handoffs at the pinned identity origin", async () => {
  assert.equal(await harness({ logout_url: null }).client.logout(), null);
  assert.equal(await harness(() => new Response(null, { status: 204 })).client.logout(), null);
  const destination = "https://identity.example.invalid/realms/test/logout?client_id=synthetic";
  const h = harness({ logout_url: destination }, { idpOrigin: "https://identity.example.invalid" });
  assert.equal(await h.client.logout(), destination);
  assert.equal(h.calls[0].url, "/v1/auth/logout"); assert.equal(h.calls[0].init.body, "{}");
  assert.equal(h.calls[0].init.headers["X-CSRF-Token"], session.csrf_token);
  assert.equal(h.calls[0].init.headers["Idempotency-Key"], undefined);
  for (const options of [{}, { idpOrigin: "https://other.invalid" }]) {
    await assert.rejects(harness({ logout_url: destination }, options).client.logout(), { code: "outcome_unknown" });
  }
});

test("renewal and all-session logout use the current CSRF binding without replaying a mutation", async () => {
  const renewed = harness(sessionWire);
  assert.equal((await renewed.client.renew()).context_id, "ctx1");
  assert.equal(renewed.calls[0].url, "/v1/auth/renew");
  const ended = harness(() => new Response(null, { status: 204 }));
  await ended.client.logoutAll();
  assert.equal(ended.calls[0].url, "/v1/auth/logout-all");
  for (const call of [renewed.calls[0], ended.calls[0]]) {
    assert.equal(call.init.method, "POST"); assert.equal(call.init.body, "{}");
    assert.equal(call.init.headers["X-CSRF-Token"], session.csrf_token);
  }
  const lost = harness(() => { throw new TypeError("network"); });
  await assert.rejects(lost.client.renew(), { code: "outcome_unknown" });
  assert.equal(lost.calls.length, 1);
});
