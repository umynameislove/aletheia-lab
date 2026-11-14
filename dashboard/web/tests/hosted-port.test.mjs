import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedClient, HostedRequestDiscarded } from "../src/service/hosted-client.ts";
import { createHostedPort } from "../src/service/hosted-port.ts";
import { checkProductView } from "../src/service/contract.ts";

const VERSION = "product-api/v1-draft", SNAPSHOT = "upload-snapshot-" + "a".repeat(64);
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = { workspace_id: "ws1", allowed_actions: ["analyze"], upload_bundle_bytes: 1024, question_chars: 2000, job_poll_min_ms: 1 };
const view = (result = "result1") => ({ schema_version: "p6-product-view/v1", demo_only: false, mode: "project_audit", visibility: "diagnosis",
  project: { id: "project1", display_name: "Synthetic" }, runtime: { provider: "local_evidence", model: "synthetic/v1", external_call: false },
  snapshot: { id: SNAPSHOT, baseline_id: SNAPSHOT, sha256: "a".repeat(64), metric_definitions: [], metric_changes: [] },
  result: { id: result, status: "complete", causal_status: "unverified", disposition: "abstain", summary: "Observed.",
    allowed_wording: "Observed.", forbidden_wording: "Cause.", caveat: "Initial snapshot.",
    denominators: { independent_families: null, contexts: 1, outputs: 1, claims: 0 } },
  conversation: { id: "conversation1", turns: [] }, evidence: [], claims: [], graph: { nodes: [], edges: [] } });
const receipt = (job = "job1") => ({ workspace_id: "ws1", job_id: job, request_id: "req1", status_url: `/v1/workspaces/ws1/jobs/${job}` });
const job = (jobId = "job1", status = "succeeded") => ({ workspace_id: "ws1", job_id: jobId, status, progress: null });
const output = (jobId = "job1", result = view()) => ({ workspace_id: "ws1", job_id: jobId, operation: "analyze", result });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function mockClient() {
  const calls = [];
  return { calls, checkView: checkProductView,
    submitJob: async (...args) => { calls.push(["submit", ...args]); return receipt(); },
    pollJob: async (...args) => { calls.push(["poll", ...args]); return job(); },
    loadOutput: async (...args) => { calls.push(["output", ...args]); return output(); },
  };
}
function setup(client = mockClient(), extra = {}) {
  const port = createHostedPort();
  const context = { session, workspace: "ws1", caps, client, isCurrent: () => true, ...extra };
  port.setContext(context);
  return { port, context, client };
}

test("detached ProductPort analysis uses only the real hosted client flow with a fresh request key", async () => {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async (url, init) => {
    calls.push({ url, init });
    if (init.method === "POST") return json({ schema_version: VERSION, ...receipt(), status: "queued" }, 202);
    if (url.endsWith("/output")) return json({ schema_version: VERSION, ...output() });
    return json({ schema_version: VERSION, ...job() });
  } });
  const { port } = setup(client);
  const analyze = port.analyze_observations;
  assert.equal((await analyze("project1", SNAPSHOT, "What is observed?")).result.id, "result1");
  assert.equal(port.kind, "live"); assert.equal(port.latestJobId(), "job1");
  assert.deepEqual(calls.map(x => x.url), ["/v1/workspaces/ws1/jobs", "/v1/workspaces/ws1/jobs/job1", "/v1/workspaces/ws1/jobs/job1/output"]);
  assert.deepEqual(JSON.parse(calls[0].init.body), { operation: "analyze", snapshot_id: SNAPSHOT, question: "What is observed?" });
  assert.match(calls[0].init.headers["Idempotency-Key"], /^[0-9a-f-]{36}$/);
  assert.equal((await port.demo_view()).demo_only, false);
  const read = port.view;
  assert.equal((await read("result1")).result.id, "result1");
  assert.equal(calls.at(-1).url, "/v1/workspaces/ws1/jobs/job1/output");
});

test("reopening uses supplied job and expected IDs, rechecks authority and never submits", async () => {
  const client = mockClient();
  const { port } = setup(client, { initialJobId: "job1", initialProjectId: "project1", initialSnapshotId: SNAPSHOT });
  await assert.rejects(port.demo_view(), { code: "no_result" });
  const read = port.view;
  assert.equal((await read("result1")).result.id, "result1");
  assert.deepEqual(client.calls, [["output", "ws1", "job1", { operation: "analyze", projectId: "project1", snapshotId: SNAPSHOT }]]);
  await assert.rejects(port.view("different_result"), { code: "response_out_of_scope" });
  const noPointer = setup(mockClient(), { initialJobId: "job1" });
  await assert.rejects(noPointer.port.view("result1"), { code: "no_result" });
  assert.deepEqual(noPointer.client.calls, []);
});

test("initial actual view is checked and supplies pointer scope without fabricating preview data", async () => {
  const h = setup(mockClient(), { initialView: view(), initialJobId: "job1" });
  assert.deepEqual(await h.port.demo_view(), view()); assert.deepEqual(h.client.calls, []);
  await h.port.view("result1");
  assert.deepEqual(h.client.calls, [], "the result already on screen is immutable: opening it again reads nothing (Kiên, 13/11)");
  // a result opened from its link alone is read with the link's own snapshot scope
  const linked = setup(mockClient(), { initialJobId: "job1", initialProjectId: "project1", initialSnapshotId: SNAPSHOT });
  await linked.port.view("result1");
  assert.equal(linked.client.calls[0][3].snapshotId, SNAPSHOT);
  const invalid = createHostedPort();
  assert.throws(() => invalid.setContext({ ...h.context, initialView: { ...view(), demo_only: true } }), { code: "response_out_of_scope" });
  assert.equal(invalid.context(), null);
  assert.throws(() => h.port.setContext({ ...h.context, caps: { ...caps, workspace_id: "ws2" } }), { code: "response_invalid" });
  assert.equal(h.port.context(), null); assert.equal(h.port.latestJobId(), null);
});

test("local-only methods stay unavailable, and the four bindings stay off until the workspace offers them", async () => {
  const h = setup();
  for (const [method, args] of [
    ["preview_import", ["private/local/path"]], ["confirm_import", ["preview1", {}]],
    ["analyze_mock", ["project1", SNAPSHOT, "Question?"]],
    // no projects.refresh / projects.follow_up / exports.download / projects.logical_delete feature here;
    // their positive and negative coverage is in hosted-bindings.test.mjs
    ["refresh", ["project1", { source_id: "source1" }]], ["follow_up", ["result1", "claim", "claim1", "Question?"]],
    ["export_report", ["result1", "json"]], ["delete_project", ["project1"]],
  ]) {
    const detached = h.port[method];
    await assert.rejects(detached(...args), { code: "feature_unavailable" });
  }
  assert.deepEqual(h.client.calls, []);
  await assert.rejects(h.port.demo_view(), { code: "no_result" });
});

test("wrong result/project/snapshot/demo answers cannot become the current view", async () => {
  for (const changed of [
    { result: { ...view().result, id: "result2" } }, { project: { id: "project2", display_name: "Other" } },
    { snapshot: { ...view().snapshot, id: "snapshot2" } }, { demo_only: true },
  ]) {
    const client = mockClient(); client.loadOutput = async () => output("job1", { ...view(), ...changed });
    const { port } = setup(client, { initialJobId: "job1", initialProjectId: "project1", initialSnapshotId: SNAPSHOT });
    await assert.rejects(port.view("result1"), { code: "response_out_of_scope" });
    await assert.rejects(port.demo_view(), { code: "no_result" });
  }
});

test("each analysis await discards late success or failure after context replacement", async () => {
  for (const stage of ["submitJob", "pollJob", "loadOutput"]) {
    for (const rejectReply of [false, true]) {
      const client = mockClient(); let resolve, reject, entered;
      const reached = new Promise(r => { entered = r; });
      const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
      client[stage] = async () => { entered(); return pending; };
      const h = setup(client);
      const analysis = h.port.analyze_observations("project1", SNAPSHOT, "What is observed?");
      await reached;
      if (stage !== "submitJob") assert.equal(h.port.latestJobId(), "job1");
      h.port.setContext({ ...h.context, session: { ...session, user_id: "actor2", context_id: "ctx2" } });
      if (rejectReply) reject(new Error("synthetic old-context error"));
      else resolve(stage === "submitJob" ? receipt() : stage === "pollJob" ? job() : output());
      await assert.rejects(analysis, HostedRequestDiscarded);
      assert.equal(h.port.latestJobId(), null);
      await assert.rejects(h.port.demo_view(), { code: "no_result" });
      assert.equal(client.calls.some(call => call[0] === "output"), false);
    }
  }
});

test("wipe and caller fence prevent cached or late reads from crossing contexts", async () => {
  let current = true;
  const h = setup(mockClient(), { initialView: view(), initialJobId: "job1", isCurrent: () => current });
  assert.equal(h.port.capabilities(), caps);
  current = false;
  await assert.rejects(h.port.demo_view(), HostedRequestDiscarded);
  await assert.rejects(h.port.view("result1"), HostedRequestDiscarded);
  assert.deepEqual(h.client.calls, []);
  h.port.clearContext();
  assert.equal(h.port.context(), null); assert.equal(h.port.capabilities(), null); assert.equal(h.port.latestJobId(), null);
});

test("failed terminal jobs retain accepted job identity and never fetch or invent output", async () => {
  const client = mockClient(); client.pollJob = async () => job("job1", "failed");
  const { port } = setup(client);
  await assert.rejects(port.analyze_observations("project1", SNAPSHOT, "What is observed?"), { code: "analysis_failed" });
  assert.equal(port.latestJobId(), "job1");
  assert.equal(client.calls.filter(call => call[0] === "submit").length, 1);
  assert.equal(client.calls.some(call => call[0] === "output"), false);
  await assert.rejects(port.demo_view(), { code: "no_result" });
});

test("result pointers remain bounded and clear when a new context is installed", async () => {
  let sequence = 0;
  const client = mockClient();
  client.submitJob = async () => receipt("job" + ++sequence);
  client.pollJob = async (_workspace, id) => job(id);
  client.loadOutput = async (_workspace, id) => output(id, view("result" + id.slice(3)));
  const h = setup(client);
  for (let i = 0; i < 17; i++) await h.port.analyze_observations("project1", SNAPSHOT, "What is observed?");
  await assert.rejects(h.port.view("result1"), { code: "no_result" });
  assert.equal((await h.port.view("result2")).result.id, "result2");
  h.port.setContext(h.context);
  await assert.rejects(h.port.view("result2"), { code: "no_result" });
  assert.equal(h.port.latestJobId(), null);
});
