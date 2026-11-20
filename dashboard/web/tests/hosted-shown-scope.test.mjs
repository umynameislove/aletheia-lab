import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createHostedBootstrap } from "../src/service/hosted-bootstrap.ts";
import { createHostedClient } from "../src/service/hosted-client.ts";
import { createSessionGuard } from "../src/service/session.ts";

/* The object on screen is what a confirmation is about (Kiên R4.11): an answer about one result never
   confirms another, even when it arrives after the screen changed. */

const VERSION = "product-api/v1-draft";
const HEADER = JSON.parse(readFileSync(new URL("./fixtures/k10-graph-r3/result-header.json", import.meta.url), "utf8"));
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const session = { schema_version: VERSION,
  actor: { user_id: "actor", display_name: "Server account", email: "synthetic@example.invalid", email_verified: true },
  session: { context_id: "ctx", expires_at: "2030-01-01T00:00:00Z" }, csrf_token: "synthetic-csrf-token",
  onboarding: "ready", last_workspace_id: null, can_create_workspace: false };
const caps = { schema_version: VERSION, workspace_id: "a", workspace_name: "Server workspace", deployment_mode: "hosted",
  membership: { role: "analyst", revision: 1 }, analysis_backends: ["local_evidence"], supported_source_types: ["zip_bundle"],
  supported_export_formats: [], allowed_actions: ["import", "analyze"],
  features: { "catalog.paged_read": true, "jobs.execution_v1": true, "jobs.cancel": true, "jobs.retry": true },
  limits: { question_chars: 2000, upload_bundle_bytes: 1024, page_max: 50, job_poll_min_ms: 1000 } };
const other = "p6-result-" + "b".repeat(64);
const header = resultId => ({ ...HEADER, result_id: resultId, result: { ...HEADER.result, id: resultId },
  conversation_id: resultId.replace("p6-result-", "p6-conversation-") });
const scopeOf = resultId => ({ resultId, projectId: HEADER.project_id, snapshotId: HEADER.snapshot_id });

test("changing the object on screen drops an answer still on its way and asks again", async () => {
  let now = 0, requests = 0; const answers = [];
  const guard = createSessionGuard({ contextId: "ctx", authorizationLeaseMs: 4000, now: () => now, onWipe() {},
    check: () => new Promise(resolve => { requests++; answers.push(resolve); }) });
  const first = guard.check(false);
  guard.invalidate();
  assert.equal(guard.state(), "confirming");
  now = 1000; answers[0]({ status: "ok", context_id: "ctx" }); await first;
  // the answer about the previous object confirmed nothing; a new check is already on its way
  assert.equal(guard.state(), "confirming"); assert.equal(requests, 2);
  answers[1]({ status: "ok", context_id: "ctx" }); await guard.check(false);
  assert.equal(guard.state(), "active");
  // its lease counts from the new check, sent at 1000
  now = 4999; guard.onTick(true); assert.equal(guard.state(), "active");
  now = 5000; guard.onTick(true); assert.equal(guard.state(), "confirming");
});

test("a revoked or expired session is not reopened by a change of object", async () => {
  let requests = 0;
  const guard = createSessionGuard({ contextId: "ctx", authorizationLeaseMs: 4000, now: () => 0, onWipe() {},
    check: async () => { requests++; return { status: "revoked" }; } });
  await guard.check(true); assert.equal(guard.state(), "revoked");
  guard.invalidate(); assert.equal(guard.state(), "revoked"); assert.equal(requests, 1);
});

test("a result shown after another is confirmed by its own header, never by the first one's", async () => {
  const pending = new Map(), calls = [];
  const fetch = async url => {
    calls.push(url);
    if (url === "/v1/session") return json(session);
    if (url.endsWith("/capabilities")) return json(caps);
    if (url.endsWith("/header")) return new Promise(resolve => pending.set(url, resolve));
    throw new Error("unexpected " + url);
  };
  const boot = createHostedBootstrap({ fetch, onClear() {}, now: () => 0 });
  await boot.start(); await boot.selectWorkspace("a");
  const guard = boot.guard(), headerOf = id => `/v1/workspaces/a/results/${id}/header`;
  const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };

  boot.watchResult(scopeOf(HEADER.result_id));
  assert.equal(guard.state(), "confirming");
  await settle(); assert.ok(pending.has(headerOf(HEADER.result_id)));

  // the screen moves to another result while the first header is still on its way
  boot.watchResult(scopeOf(other));
  pending.get(headerOf(HEADER.result_id))(json(header(HEADER.result_id)));
  await settle();
  assert.equal(guard.state(), "confirming", "the first result's header confirmed the second");
  assert.ok(pending.has(headerOf(other)), "the second result's own header was not read");

  pending.get(headerOf(other))(json(header(other)));
  await settle();
  assert.equal(guard.state(), "active");

  // naming the same result again (the port reporting what it shows) asks nothing new
  const before = calls.length;
  boot.watchResult(scopeOf(other)); await settle();
  assert.equal(calls.length, before); assert.equal(guard.state(), "active");
  // leaving the result shows nothing private of it, so nothing is asked either
  boot.watchResult(null); await settle();
  assert.equal(calls.length, before); assert.equal(guard.state(), "active");
  boot.stop();
});

test("job history and actions stay on one job and a retry keeps its intent", async () => {
  const calls = [], job_id = "job1";
  const client = createHostedClient({ session: { user_id: "actor", context_id: "ctx", csrf_token: "synthetic-csrf" }, isCurrent: () => true,
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      if (url.endsWith("/attempts")) return json({ schema_version: VERSION, workspace_id: "a", job_id, retry_of_job_id: null,
        items: [{ epoch: 1, started_at: null, heartbeat_at: null, lease_until: null, finished_at: null, outcome: "cancel_requested", failure_kind: "cancelled" }] });
      if (url.endsWith("/cancel")) return json({ schema_version: VERSION, workspace_id: "a", job_id, status: "cancel_requested", progress: null, retryable: null });
      return json({ schema_version: VERSION, workspace_id: "a", job_id: "job2", status: "queued", request_id: "req1", status_url: "/v1/workspaces/a/jobs/job2" }, 202);
    } });
  assert.equal((await client.loadJobAttempts("a", caps, job_id)).items[0].outcome, "cancel_requested");
  assert.equal((await client.cancelJob("a", caps, job_id)).status, "cancel_requested");
  await client.retryJob("a", caps, job_id, "same_retry_intent_123");
  await client.retryJob("a", caps, job_id, "same_retry_intent_123");
  assert.ok(!calls[0].url.includes("?"));
  assert.equal(calls[1].body, "{}"); assert.equal(new Headers(calls[1].headers).get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(new Headers(calls[2].headers).get("Idempotency-Key"), new Headers(calls[3].headers).get("Idempotency-Key"));
  await assert.rejects(async () => client.retryJob("a", { ...caps, features: {} }, job_id, "same_retry_intent_123"), { code: "feature_unavailable" });
});
