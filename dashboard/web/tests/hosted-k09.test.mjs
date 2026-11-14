/* K09 job controls (jobs.execution_v1, jobs.cancel, jobs.retry), from the producer models in Kiên's R3
   source: hosted_contract/job_attempts.py, job_page.py and hosted/job_http.py. Synthetic answers only. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedClient } from "../src/service/hosted-client.ts";

const VERSION = "product-api/v1-draft", WS = "ws1", JOB = "job_parent_1";
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = (features = { "jobs.execution_v1": true, "jobs.cancel": true, "jobs.retry": true }) => ({ workspace_id: WS,
  allowed_actions: ["analyze"], upload_bundle_bytes: 1024, question_chars: 2000, job_poll_min_ms: 10, page_max: 100, features });
const json = (value, status = 200, type = "application/json") => new Response(JSON.stringify(value), { status, headers: { "Content-Type": type } });
const problem = (code, status) => json({ schema_version: VERSION, type: "urn:test:" + code, title: "Refused", status, code,
  detail: "Safe synthetic refusal.", request_id: "req1" }, status, "application/problem+json");
const attempt = (epoch, over = {}) => ({ epoch, started_at: "2026-11-13T08:00:00Z", heartbeat_at: "2026-11-13T08:00:05Z",
  lease_until: "2026-11-13T08:02:00Z", finished_at: "2026-11-13T08:00:09Z", outcome: "failed", failure_kind: "transient", ...over });
const history = (items, over = {}) => ({ schema_version: VERSION, workspace_id: WS, job_id: JOB, retry_of_job_id: null, items, ...over });
const job = (over = {}) => ({ schema_version: VERSION, workspace_id: WS, job_id: JOB, status: "running", progress: null, ...over });
function harness(answer) {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async (url, init) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body }); return typeof answer === "function" ? answer(url, init) : answer.clone(); } });
  return { client, calls };
}

test("J1 the attempt history is read as the service bounds it: three ordered epochs, only the last still going", async () => {
  const ok = harness(json(history([attempt(1), attempt(2, { outcome: "running", finished_at: null, failure_kind: null })], { retry_of_job_id: "job_root" })));
  const read = await ok.client.loadJobAttempts(WS, caps(), JOB);
  assert.equal(ok.calls[0].url, `/v1/workspaces/${WS}/jobs/${JOB}/attempts`); assert.equal(ok.calls[0].method, "GET");
  assert.equal(read.retry_of_job_id, "job_root"); assert.deepEqual(read.items.map(a => [a.epoch, a.outcome]), [[1, "failed"], [2, "running"]]);
  const bad = [
    history([attempt(2), attempt(1)]), history([attempt(1), attempt(1)]), history([1, 2, 3, 4].map(n => attempt(Math.min(n, 3)))),
    history([attempt(1, { outcome: "running" })]), history([attempt(1, { finished_at: null })]),
    history([attempt(1, { outcome: "running", finished_at: null, failure_kind: null }), attempt(2)]),
    history([attempt(1, { outcome: "paused" })]), history([attempt(1, { failure_kind: "unknown" })]), history([attempt(1, { started_at: "yesterday" })]),
  ];
  for (const value of bad) await assert.rejects(harness(json(value)).client.loadJobAttempts(WS, caps(), JOB), { code: "response_invalid" }, JSON.stringify(value.items));
  await assert.rejects(harness(json(history([], { job_id: "job_other" }))).client.loadJobAttempts(WS, caps(), JOB), { code: "response_out_of_scope" });
});

test("J2 cancel sends an empty body with the current CSRF and no request key; cancel_requested is not a stop", async () => {
  const h = harness(json(job({ status: "cancel_requested" })));
  const answer = await h.client.cancelJob(WS, caps(), JOB);
  assert.equal(answer.status, "cancel_requested");
  assert.equal(h.calls[0].url, `/v1/workspaces/${WS}/jobs/${JOB}/cancel`); assert.equal(h.calls[0].method, "POST");
  assert.equal(h.calls[0].body, "{}"); assert.equal(h.calls[0].headers["X-CSRF-Token"], "synthetic-csrf-token");
  assert.equal(h.calls[0].headers["Idempotency-Key"], undefined);
  // a 2xx this client cannot trust may still have changed the job: its outcome is unknown, never "cancelled"
  await assert.rejects(harness(json(job({ job_id: "job_other", status: "cancelled" }))).client.cancelJob(WS, caps(), JOB), { code: "outcome_unknown" });
});

test("J3 retry carries its own request key, answers with a new linked job, and a state conflict is a refusal", async () => {
  const receipt = { schema_version: VERSION, workspace_id: WS, job_id: "job_child_1", status: "queued", request_id: "req_retry", status_url: `/v1/workspaces/${WS}/jobs/job_child_1` };
  const h = harness(json(receipt, 202));
  const key = "retry-key-0123456789abcdef";
  assert.equal((await h.client.retryJob(WS, caps(), JOB, key)).job_id, "job_child_1");
  assert.equal(h.calls[0].url, `/v1/workspaces/${WS}/jobs/${JOB}/retry`); assert.equal(h.calls[0].body, "{}");
  assert.equal(h.calls[0].headers["Idempotency-Key"], key); assert.equal(h.calls[0].headers["X-CSRF-Token"], "synthetic-csrf-token");
  await assert.rejects(harness(json({ ...receipt, job_id: JOB, status_url: `/v1/workspaces/${WS}/jobs/${JOB}` }, 202)).client.retryJob(WS, caps(), JOB, key),
    { code: "outcome_unknown" }, "a retry that names the same job is not a retry; what the service did stays unknown, so the key is kept");
  await assert.rejects(harness(problem("job_state_conflict", 409)).client.retryJob(WS, caps(), JOB, key), { code: "job_state_conflict" });
  await assert.rejects(harness(() => { throw new TypeError("network"); }).client.retryJob(WS, caps(), JOB, key), { code: "outcome_unknown" },
    "a lost answer leaves the retry's outcome unknown, so the same key is pressed again");
});

test("J4 each control needs its own workspace feature; nothing is sent without it", async () => {
  const h = harness(json(job()));
  await assert.rejects(async () => h.client.loadJobAttempts(WS, caps({}), JOB), { code: "feature_unavailable" });
  await assert.rejects(async () => h.client.cancelJob(WS, caps({ "jobs.retry": true }), JOB), { code: "feature_unavailable" });
  await assert.rejects(async () => h.client.retryJob(WS, caps({ "jobs.cancel": true }), JOB, "retry-key-0123456789abcdef"), { code: "feature_unavailable" });
  await assert.rejects(async () => h.client.cancelJob("other", caps(), JOB), { code: "feature_unavailable" });
  assert.equal(h.calls.length, 0);
});

test("J5 a job's retry decision is the service's: absent or null stays unknown, never yes", async () => {
  const read = over => harness(json(job({ status: "failed", ...over }))).client.loadJob(WS, JOB);
  assert.equal((await read({})).retryable, null);
  assert.equal((await read({ retryable: null })).retryable, null);
  assert.equal((await read({ retryable: true, attempt: 3, max_attempts: 3 })).retryable, true);
  await assert.rejects(read({ retryable: "yes" }), { code: "response_invalid" });
  await assert.rejects(read({ attempt: 0 }), { code: "response_invalid" });
});

test("H1 a hidden or offline tab sends no job polls; the job is read again once the tab is shown and online, never resubmitted", async () => {
  const page = Object.assign(new EventTarget(), { visibilityState: "hidden" });
  const had = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const bus = new EventTarget();
  globalThis.document = page;
  Object.assign(globalThis, { addEventListener: bus.addEventListener.bind(bus), removeEventListener: bus.removeEventListener.bind(bus), dispatchEvent: bus.dispatchEvent.bind(bus) });
  Object.defineProperty(globalThis, "navigator", { value: { onLine: false }, configurable: true });
  try {
    let n = 0;
    const h = harness(() => json(job({ status: ++n >= 2 ? "succeeded" : "running" })));
    const polling = h.client.pollJob(WS, JOB, caps(), { timeoutMs: 1000 });
    await new Promise(done => setTimeout(done, 1500));
    assert.equal(h.calls.length, 0, "nothing is read while hidden, and the time away does not run out the client deadline");
    page.visibilityState = "visible"; page.dispatchEvent(new Event("visibilitychange"));
    await new Promise(done => setTimeout(done, 50));
    assert.equal(h.calls.length, 0, "still offline: nothing is read");
    globalThis.navigator.onLine = true; globalThis.dispatchEvent(new Event("online"));
    assert.equal((await polling).status, "succeeded");
    assert.ok(h.calls.length >= 2 && h.calls.every(call => call.method === "GET" && call.url === `/v1/workspaces/${WS}/jobs/${JOB}`));
  } finally {
    delete globalThis.document; delete globalThis.addEventListener; delete globalThis.removeEventListener; delete globalThis.dispatchEvent;
    if (had) Object.defineProperty(globalThis, "navigator", had); else delete globalThis.navigator;
  }
});

test("J9 jobs.conflict_v1: a latest conflict attempt may name its project and revision; nowhere else", async () => {
  const conflicted = attempt(1, { failure_kind: "conflict" });
  const named = await harness(json(history([conflicted], { conflict: { project_id: "project1", current_revision: 5 } }))).client.loadJobAttempts(WS, caps(), JOB);
  assert.equal(named.items[0].failure_kind, "conflict");
  assert.deepEqual(named.conflict, { project_id: "project1", current_revision: 5 });
  assert.equal((await harness(json(history([conflicted]))).client.loadJobAttempts(WS, caps(), JOB)).conflict, null);
  assert.equal((await harness(json(history([attempt(1)]))).client.loadJobAttempts(WS, caps(), JOB)).conflict, null);
  for (const value of [
    history([attempt(1)], { conflict: { project_id: "project1", current_revision: 5 } }),
    history([conflicted, attempt(2)], { conflict: { project_id: "project1", current_revision: 5 } }),
    history([conflicted], { conflict: { project_id: "project1", current_revision: -1 } }),
    history([conflicted], { conflict: { project_id: "../x", current_revision: 5 } }),
    history([conflicted], { conflict: "project1" }),
  ]) await assert.rejects(harness(json(value)).client.loadJobAttempts(WS, caps(), JOB), { code: "response_invalid" }, JSON.stringify(value.conflict));
});
