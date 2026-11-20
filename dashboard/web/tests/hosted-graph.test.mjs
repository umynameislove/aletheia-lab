/* K10 graph pages (GET results/{id}/graph): Kiên's R3 fixture 07, a small native v2 result (10 claims,
   22 nodes, 30 edges) cut into 33 validated GraphView pages. Its cursors are scripted placeholders, not
   live tokens, and it is not a 10k or primary-capacity fixture. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHostedClient } from "../src/service/hosted-client.ts";

const dir = new URL("./fixtures/k10-graph-r3/", import.meta.url);
const read = name => JSON.parse(readFileSync(fileURLToPath(new URL(name, dir)), "utf8"));
const HEADER = read("result-header.json");
const PAGES = Array.from({ length: 33 }, (_, i) => read(`graph-page-${String(i + 1).padStart(2, "0")}.json`));
const WS = HEADER.workspace_id;
const scope = { resultId: HEADER.result_id, projectId: HEADER.project_id, snapshotId: HEADER.snapshot_id };
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const caps = (features = { "catalog.paged_read": true }) => ({ workspace_id: WS, allowed_actions: ["read"], upload_bundle_bytes: 1024,
  question_chars: 2000, job_poll_min_ms: 10, page_max: 100, features });
const json = (value, status = 200, type = "application/json") => new Response(JSON.stringify(value), { status, headers: { "Content-Type": type } });
const problem = (code, status) => json({ schema_version: "product-api/v1-draft", type: "urn:test:" + code, title: "Refused", status, code,
  detail: "Safe synthetic refusal.", request_id: "req1" }, status, "application/problem+json");
function harness(answer) {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true,
    fetch: async (url, init) => { calls.push({ url, method: init.method }); return answer(new URL(url, "http://local")); } });
  return { client, calls };
}
const byCursor = url => { const cursor = url.searchParams.get("cursor"); return json(PAGES[cursor ? Number(cursor.split("-").pop()) - 1 : 0]); };

test("G1 every page merged by ID adds up to the header's counts; reading ends only at a null cursor", async () => {
  const h = harness(byCursor);
  const nodes = new Map(), edges = new Map(); let cursor = null, pages = 0, seen = 0, last;
  do {
    last = await h.client.loadGraphPage(WS, caps(), scope, { cursor });
    for (const node of last.nodes) nodes.set(node.id, node);
    for (const edge of last.edges) edges.set(edge.id, edge);
    seen += last.nodes.length; cursor = last.next_cursor; pages++;
  } while (cursor !== null);
  assert.equal(pages, 33);
  assert.equal(nodes.size, HEADER.node_count); assert.equal(edges.size, HEADER.edge_count);
  assert.ok(seen > nodes.size, "edge pages repeat their endpoint nodes");
  assert.equal(last.truncated, true, "the last page may still say truncated; only the null cursor ends the read");
  assert.match(h.calls[0].url, new RegExp(`^/v1/workspaces/${WS}/results/${HEADER.result_id}/graph\\?limit=50$`));
  assert.equal(new URL(h.calls[1].url, "http://local").searchParams.get("cursor"), "synthetic-page-2", "the cursor is passed back as given");
  assert.ok(h.calls.every(call => call.method === "GET"));
});

test("G2 a page about another result or project, or another workspace, is refused", async () => {
  for (const over of [{ result_id: "p6-result-" + "9".repeat(64) }, { project_id: "p3-project-" + "9".repeat(64) }, { workspace_id: "other" }]) {
    await assert.rejects(harness(() => json({ ...PAGES[0], ...over })).client.loadGraphPage(WS, caps(), scope, { cursor: null }),
      { code: /response_out_of_scope|response_invalid/ }, JSON.stringify(over));
  }
});

test("G3 nodes and edges are checked one by one; a malformed or oversized page shows nothing", async () => {
  const edgePage = PAGES[13];
  const bad = [
    { ...PAGES[0], graph: { ...PAGES[0].graph, nodes: [{ ...PAGES[0].graph.nodes[0], visibility: "public" }] } },
    { ...edgePage, graph: { ...edgePage.graph, edges: [{ ...edgePage.graph.edges[0], kind: "cites" }] } },
    { ...edgePage, graph: { ...edgePage.graph, edges: [edgePage.graph.edges[0], edgePage.graph.edges[0]] } },
    { ...PAGES[0], next_cursor: "" },
    { ...PAGES[0], truncated: "no" },
    { ...PAGES[0], graph: { nodes: PAGES[0].graph.nodes } },
  ];
  for (const page of bad) await assert.rejects(harness(() => json(page)).client.loadGraphPage(WS, caps(), scope, { cursor: null }), { code: "response_invalid" });
  const many = Array.from({ length: 4 }, (_, i) => ({ ...edgePage.graph.edges[0], id: "p6-edge-" + String(i).repeat(64) }));
  await assert.rejects(harness(() => json({ ...edgePage, graph: { ...edgePage.graph, edges: many } })).client.loadGraphPage(WS, caps(), scope, { cursor: null, limit: 3 }),
    { code: "response_invalid" }, "more edges than the asked limit");
});

test("G4 an expired or invalid cursor comes back as its own code, for the reader to start again", async () => {
  for (const [code, status] of [["cursor_expired", 410], ["cursor_invalid", 400]]) {
    await assert.rejects(harness(() => problem(code, status)).client.loadGraphPage(WS, caps(), scope, { cursor: "synthetic-page-5" }), { code });
  }
});

test("G5 nothing is asked without paged reading, and a cursor outside its bounds is never sent", async () => {
  const h = harness(byCursor);
  await assert.rejects(async () => h.client.loadGraphPage(WS, caps({}), scope, { cursor: null }), { code: "feature_unavailable" });
  await assert.rejects(async () => h.client.loadGraphPage(WS, caps(), scope, { cursor: "x".repeat(4097) }), { code: "invalid_request" });
  await assert.rejects(async () => h.client.loadGraphPage(WS, caps(), scope, { cursor: null, limit: 101 }), { code: "invalid_request" });
  assert.equal(h.calls.length, 0);
});
