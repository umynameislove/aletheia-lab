/* K13 deletion status and K15 job updates (Kiên's Ban-giao-Quan-K13-K18-20261113, 01-Contract). Each runs
   through the real hosted client over a scripted fetch. K15 pacing uses the service's own poll_after_ms
   (2–3.5 s), so the polling cases run side by side. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedClient } from "../src/service/hosted-client.ts";

const VERSION = "product-api/v1-draft", WS = "ws1", JOB = "job1";
const PROJECT = "p3-project-" + "a".repeat(64), TOMBSTONE = "p6-deletion-" + "b".repeat(64);
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = (features = {}) => ({ workspace_id: WS, allowed_actions: ["read", "analyze"], upload_bundle_bytes: 1024,
  question_chars: 2000, job_poll_min_ms: 10, page_max: 100, features });
const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value),
  { status, headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json", ...headers } });
const problem = (code, status, headers) => json({ schema_version: VERSION, type: "urn:test:" + code, title: "Refused", status, code,
  detail: "Safe synthetic refusal.", request_id: "req1" }, status, headers);
function harness(answers) {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async (url, init) => {
    calls.push({ url, method: init.method, at: performance.now() });
    const answer = answers.length > 1 ? answers.shift() : answers[0];
    return typeof answer === "function" ? answer(url) : answer.clone();
  } });
  return { client, calls };
}

// K13 ------------------------------------------------------------------------------------------------
const deletion = (over = {}) => ({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, tombstone_id: TOMBSTONE, revision: 3,
  status: "retained", requested_at: "2030-01-01T00:00:00Z", eligible_at: "2030-01-31T00:00:00Z", project_retention_days: 30,
  time_basis: "delete_commit", purged_at: null, records_removed: 0, pending_objects: 2, shared_objects_retained: 1,
  raw_source_policy: "workspace_sources_retained", backup_policy: "operator_managed_not_erased_here", ...over });
const DELETION_ON = { "projects.deletion_status": true };

test("D1 the three deletion stages are read as reported, with both scope policies and the time basis", async () => {
  for (const over of [{}, { status: "purging", records_removed: 1 },
    { status: "purged", purged_at: "2030-02-01T00:00:00Z", records_removed: 7, pending_objects: 0 },
    { time_basis: "restore_checkpoint" }, { time_basis: "migration_first_seen" }]) {
    const { client, calls } = harness([json(deletion(over))]);
    const read = await client.loadDeletionStatus(WS, caps(DELETION_ON), PROJECT, TOMBSTONE);
    assert.equal(calls[0].url, `/v1/workspaces/${WS}/projects/${PROJECT}/deletion-status`);
    assert.equal(read.status, over.status ?? "retained");
    assert.equal(read.time_basis, over.time_basis ?? "delete_commit");
    assert.equal(read.raw_source_policy, "workspace_sources_retained");
    assert.equal(read.backup_policy, "operator_managed_not_erased_here");
  }
});

test("D2 a status about another deletion, an inconsistent stage or a wider erase claim is refused", async () => {
  const read = over => harness([json(deletion(over))]).client.loadDeletionStatus(WS, caps(DELETION_ON), PROJECT, TOMBSTONE);
  await assert.rejects(read({ tombstone_id: "p6-deletion-" + "c".repeat(64) }), { code: "response_out_of_scope" });
  await assert.rejects(read({ project_id: "p3-project-" + "c".repeat(64) }), { code: "response_out_of_scope" });
  await assert.rejects(read({ status: "purged" }), { code: "response_invalid" }, "purged without purged_at");
  await assert.rejects(read({ purged_at: "2030-02-01T00:00:00Z" }), { code: "response_invalid" }, "retained with purged_at");
  await assert.rejects(read({ raw_source_policy: "workspace_sources_erased" }), { code: "response_invalid" });
  await assert.rejects(read({ backup_policy: "erased" }), { code: "response_invalid" });
  await assert.rejects(read({ time_basis: "guessed" }), { code: "response_invalid" });
  await assert.rejects(read({ status: "deleted" }), { code: "response_invalid" });
});

test("D3 nothing is asked when the workspace does not report deletion status", async () => {
  const { client, calls } = harness([json(deletion())]);
  await assert.rejects(async () => client.loadDeletionStatus(WS, caps({}), PROJECT, TOMBSTONE), { code: "feature_unavailable" });
  assert.deepEqual(calls, []);
});

// K15 ------------------------------------------------------------------------------------------------
const POLLING = { "jobs.polling_v1": true };
const job = (status = "running") => ({ schema_version: VERSION, workspace_id: WS, job_id: JOB, status, progress: null, operation: "analyze",
  created_at: "2030-01-01T00:00:00Z", deadline_at: "2030-01-01T00:02:00Z", terminal_at: null, attempt: 1, max_attempts: 3,
  retryable: null, output_url: null });
const update = (disposition, revision, status = "running", over = {}) => ({ schema_version: VERSION, workspace_id: WS, job_id: JOB, revision,
  disposition, job: disposition === "unchanged" ? null : job(status), cursor: "opaque-" + revision,
  poll_after_ms: ["succeeded", "failed", "cancelled", "expired"].includes(status) ? null : 2000, authorization_valid_for_ms: 4000, ...over });
const cursorOf = url => new URL(url, "https://x.invalid").searchParams.get("cursor");

test("U the update route keeps its cursor and the service's pacing, and recovers only what it may", { concurrency: true }, async t => {
  const cases = [];
  cases.push(t.test("U1 snapshot, unchanged, resync: unchanged keeps the job held, the cursor continues, pacing is the service's", async () => {
    const { client, calls } = harness([json(update("snapshot", 2)), json(update("unchanged", 2)), json(update("resync", 5, "succeeded"))]);
    const done = await client.pollJob(WS, JOB, caps(POLLING));
    assert.equal(done.status, "succeeded");
    assert.deepEqual(calls.map(c => new URL(c.url, "https://x.invalid").pathname), Array(3).fill(`/v1/workspaces/${WS}/jobs/${JOB}/updates`));
    assert.deepEqual(calls.map(c => cursorOf(c.url)), [null, "opaque-2", "opaque-2"]);
    assert.ok(calls[1].at - calls[0].at >= 1990 && calls[2].at - calls[1].at >= 1990, "waits poll_after_ms, not the workspace minimum");
  }));
  cases.push(t.test("U2 an older revision does not replace the job held", async () => {
    const { client } = harness([json(update("snapshot", 3, "running")), json(update("snapshot", 2, "succeeded")), json(update("snapshot", 4, "failed"))]);
    assert.equal((await client.pollJob(WS, JOB, caps(POLLING))).status, "failed", "the stale 'succeeded' at revision 2 was not taken");
  }));
  cases.push(t.test("U3 an expired cursor is dropped and one fresh snapshot is read, at once", async () => {
    const { client, calls } = harness([json(update("snapshot", 2)), problem("cursor_expired", 410), json(update("snapshot", 3, "succeeded"))]);
    assert.equal((await client.pollJob(WS, JOB, caps(POLLING))).status, "succeeded");
    assert.deepEqual(calls.map(c => cursorOf(c.url)), [null, "opaque-2", null]);
    assert.ok(calls[2].at - calls[1].at < 1000, "no backoff for a cursor restart");
  }));
  cases.push(t.test("U4 a 429 waits at least its Retry-After before asking again", async () => {
    const { client, calls } = harness([problem("rate_limited", 429, { "Retry-After": "1" }), json(update("snapshot", 2, "succeeded"))]);
    assert.equal((await client.pollJob(WS, JOB, caps(POLLING))).status, "succeeded");
    assert.ok(calls[1].at - calls[0].at >= 990, "Retry-After is a minimum");
  }));
  cases.push(t.test("U5 an answer that arrives after its four-second authorization window is not used", async () => {
    let first = true;
    const { client, calls } = harness([async () => {
      if (first) { first = false; await new Promise(resolve => setTimeout(resolve, 4_100)); return json(update("snapshot", 2, "failed")); }
      return json(update("snapshot", 3, "succeeded"));
    }]);
    assert.equal((await client.pollJob(WS, JOB, caps(POLLING))).status, "succeeded", "the late 'failed' was discarded");
    assert.equal(calls.length, 2);
  }));
  cases.push(t.test("U6 a refusal stops watching: no cursor reset hides lost access", async () => {
    const { client, calls } = harness([json(update("snapshot", 2)), problem("permission_denied", 403)]);
    await assert.rejects(client.pollJob(WS, JOB, caps(POLLING)), { code: "permission_denied" });
    assert.equal(calls.length, 2);
  }));
  cases.push(t.test("U7 an unchanged answer to a request without a cursor is not trusted", async () => {
    const { client, calls } = harness([json(update("unchanged", 2))]);
    await assert.rejects(client.pollJob(WS, JOB, caps(POLLING)), { code: "response_invalid" });
    assert.equal(calls.length, 1);
  }));
  cases.push(t.test("U8 an older server without jobs.polling_v1 is watched through GET jobs/{id} as before", async () => {
    const { client, calls } = harness([json(job("running")), json(job("succeeded"))]);
    assert.equal((await client.pollJob(WS, JOB, caps({}))).status, "succeeded");
    assert.deepEqual(calls.map(c => c.url), Array(2).fill(`/v1/workspaces/${WS}/jobs/${JOB}`));
  }));
  await Promise.all(cases);
});
