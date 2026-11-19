/* K10 bounded reads (catalog.paged_read, docs/k08-k12-integration.md in Kiên's branch): a result too large
   to open whole is read as its header, claim pages and one evidence item per claim relation. Shapes follow
   the producer's producer-20261112-k08-k12 fixtures; every answer is held to the result, project and
   snapshot it was asked about. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHostedClient } from "../src/service/hosted-client.ts";

const VERSION = "product-api/v1-draft", WS = "ws1";
const RESULT = "p6-result-" + "a".repeat(64), PROJECT = "p3-project-" + "b".repeat(64), SNAPSHOT = "p3-snapshot-" + "c".repeat(64);
const CLAIM = "p6-claim-" + "d".repeat(64), EVIDENCE = "p3-evidence-" + "e".repeat(64);
const scope = { resultId: RESULT, projectId: PROJECT, snapshotId: SNAPSHOT };
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = (features = { "catalog.paged_read": true }) => ({ workspace_id: WS, allowed_actions: ["read"], upload_bundle_bytes: 1024,
  question_chars: 2000, job_poll_min_ms: 10, page_max: 100, features });
const json = (value, status = 200, type = "application/json") => new Response(JSON.stringify(value), { status, headers: { "Content-Type": type } });
const problem = (code, status) => json({ schema_version: VERSION, type: "urn:test:" + code, title: "Refused", status, code,
  detail: "Safe synthetic refusal.", request_id: "req1" }, status, "application/problem+json");
const header = (over = {}) => ({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, result_id: RESULT, snapshot_id: SNAPSHOT,
  view_schema_version: "p6-product-view/v1", project: { id: PROJECT, display_name: "Synthetic" },
  runtime: { provider: "local_evidence", model: "upload-snapshot-metric-observation/v2", external_call: false },
  result: { id: RESULT, status: "complete", disposition: "abstain", causal_status: "unverified", summary: "Observed.",
    denominators: { independent_families: null, contexts: 1, outputs: 1, claims: 120000 },
    allowed_wording: "Observed.", forbidden_wording: "Cause.", caveat: "Aggregate, not a sample." },
  conversation_id: "conversation1", turn_count: 1, claim_count: 120000, evidence_count: 240000, node_count: 9, edge_count: 8, ...over });
const claim = (id = CLAIM, over = {}) => ({ id, turn_id: "p6-turn-" + "f".repeat(64), text: "Observed series.", claim_type: "evidence_statement",
  epistemic_state: "observed", support: "fully_supported", citation_ids: [EVIDENCE], counterevidence_ids: [],
  missing_evidence: [], max_strength: "observation", ...over });
const page = (items, over = {}) => ({ schema_version: VERSION, workspace_id: WS, items, next_cursor: null, total: items.length,
  total_kind: "exact", project_id: PROJECT, result_id: RESULT, snapshot_id: SNAPSHOT, ...over });
const evidence = (over = {}) => ({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, result_id: RESULT,
  evidence_id: EVIDENCE, claim_id: CLAIM, relation: "citation",
  evidence: { id: EVIDENCE, kind: "metric_observation", title: "Observed metric change", text: "Counted.",
    reproduction_ref: { record_id: "p3-comparison-" + "1".repeat(64), record_kind: "snapshot_comparison" },
    source_sha256: "2".repeat(64), visibility: "diagnosis", redacted: false }, ...over });
function harness(answer) {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true,
    fetch: async (url, init) => { calls.push({ url, method: init.method }); return typeof answer === "function" ? answer(url) : answer.clone(); } });
  return { client, calls };
}

test("P1 the header gives the whole result's exact counts and is held to the asked result, project and snapshot", async () => {
  const ok = harness(json(header()));
  const read = await ok.client.loadResultHeader(WS, caps(), scope);
  assert.equal(ok.calls[0].url, `/v1/workspaces/${WS}/results/${RESULT}/header`);
  assert.deepEqual(read.counts, { turns: 1, claims: 120000, evidence: 240000, nodes: 9, edges: 8 });
  assert.equal(read.runtime.model, "upload-snapshot-metric-observation/v2");
  for (const over of [{ snapshot_id: "p3-snapshot-" + "9".repeat(64) }, { project_id: "p3-project-" + "9".repeat(64) }, { workspace_id: "ws2" }]) {
    await assert.rejects(harness(json(header(over))).client.loadResultHeader(WS, caps(), scope), { code: /response_out_of_scope|response_invalid/ }, JSON.stringify(over));
  }
  await assert.rejects(harness(json(header({ claim_count: -1 }))).client.loadResultHeader(WS, caps(), scope), { code: "response_invalid" });
  await assert.rejects(harness(json(header({ claim_count: null }))).client.loadResultHeader(WS, caps(), scope), { code: "response_invalid" });
});

test("P2 nothing is asked when the workspace does not offer paged reading", async () => {
  const off = harness(json(header()));
  await assert.rejects(async () => off.client.loadResultHeader(WS, caps({}), scope), { code: "feature_unavailable" });
  await assert.rejects(async () => off.client.loadClaimPage(WS, caps({ "catalog.paged_read": false }), scope, { cursor: null, search: "" }), { code: "feature_unavailable" });
  await assert.rejects(async () => off.client.loadClaimEvidence(WS, caps({}), scope, EVIDENCE, CLAIM), { code: "feature_unavailable" });
  assert.deepEqual(off.calls, []);
});

test("P3 a claim page sends limit, cursor and search, and comes back only for its own scope", async () => {
  const second = "p6-claim-" + "3".repeat(64);
  const { client, calls } = harness(json(page([claim(), claim(second)], { next_cursor: "opaque.cursor-1", total: null, total_kind: "unknown" })));
  const read = await client.loadClaimPage(WS, caps(), scope, { cursor: "opaque.cursor-0", search: "loss spike" });
  const url = new URL(calls[0].url, "https://x.invalid");
  assert.equal(url.pathname, `/v1/workspaces/${WS}/results/${RESULT}/claims`);
  assert.deepEqual(Object.fromEntries(url.searchParams), { limit: "50", cursor: "opaque.cursor-0", search: "loss spike" });
  assert.deepEqual(read.items.map(c => c.id), [CLAIM, second]);
  assert.equal(read.next_cursor, "opaque.cursor-1");
  assert.equal(read.total, null, "an unknown filtered total is never shown as a number");
  for (const over of [{ snapshot_id: "p3-snapshot-" + "9".repeat(64) }, { result_id: "p6-result-" + "9".repeat(64) }]) {
    await assert.rejects(harness(json(page([claim()], over))).client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "" }), { code: "response_out_of_scope" });
  }
  await assert.rejects(harness(json(page([claim(), claim()]))).client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "" }),
    { code: "response_invalid" }, "a page listing one claim twice");
  await assert.rejects(harness(json(page([claim(CLAIM, { citation_ids: ["../x"] })]))).client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "" }),
    { code: "response_invalid" });
  const many = Array.from({ length: 3 }, (_, n) => claim("p6-claim-" + String(n).repeat(64)));
  await assert.rejects(harness(json(page(many))).client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "", limit: 2 }),
    { code: /response_invalid|schema_unsupported/ }, "more items than asked for");
});

test("P4 out-of-bounds page requests are refused before sending; cursor errors come back as their own codes", async () => {
  const { client, calls } = harness(json(page([])));
  await assert.rejects(async () => client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "x".repeat(129) }), { code: "invalid_request" });
  await assert.rejects(async () => client.loadClaimPage(WS, caps(), scope, { cursor: null, search: "", limit: 101 }), { code: "invalid_request" });
  await assert.rejects(async () => client.loadClaimPage(WS, caps(), scope, { cursor: "", search: "" }), { code: "invalid_request" });
  assert.deepEqual(calls, []);
  for (const [code, status] of [["cursor_invalid", 400], ["cursor_expired", 410], ["response_too_large", 413]]) {
    await assert.rejects(harness(problem(code, status)).client.loadClaimPage(WS, caps(), scope, { cursor: "opaque", search: "" }), { code });
  }
});

test("P5 one evidence item is read for one claim, with its relation, and only for that claim and evidence", async () => {
  const { client, calls } = harness(json(evidence()));
  const read = await client.loadClaimEvidence(WS, caps(), scope, EVIDENCE, CLAIM);
  assert.equal(calls[0].url, `/v1/workspaces/${WS}/results/${RESULT}/evidence/${EVIDENCE}?claim_id=${CLAIM}`);
  assert.deepEqual(read, { id: EVIDENCE, kind: "metric_observation", title: "Observed metric change", text: "Counted.", redacted: false, relation: "citation" });
  const counter = await harness(json(evidence({ relation: "counterevidence" }))).client.loadClaimEvidence(WS, caps(), scope, EVIDENCE, CLAIM);
  assert.equal(counter.relation, "counterevidence");
  for (const over of [{ claim_id: "p6-claim-" + "9".repeat(64) }, { evidence_id: "p3-evidence-" + "9".repeat(64) }]) {
    await assert.rejects(harness(json(evidence(over))).client.loadClaimEvidence(WS, caps(), scope, EVIDENCE, CLAIM), { code: "response_out_of_scope" });
  }
  await assert.rejects(harness(json(evidence({ relation: "related" }))).client.loadClaimEvidence(WS, caps(), scope, EVIDENCE, CLAIM), { code: "response_invalid" });
});
