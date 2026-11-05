/* The client boundary (Q-06): version, shape and scope checks, the context fence, and the request
   lifecycle of the live port. Boundary failures must fail closed and never show demo data.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkedPort, checkPreview, checkProductView, PRODUCT_VIEW_SCHEMA } from "../src/service/contract.ts";
import { createFence, sameScope, scopeFromHash } from "../src/service/fence.ts";
import { LiveProductPort } from "../src/service/live.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = f => JSON.parse(readFileSync(join(ROOT, "src/data", f), "utf8"));
const DEMO = read("synthetic_p6_view.json");
const copy = x => structuredClone(x);
const stored = () => ({ ...copy(DEMO), demo_only: false });
const coded = code => e => e.name === "ProductError" && e.code === code;

function portAnswering(answer) {
  const base = {
    kind: "live",
    demo_view: async () => answer("demo_view"),
    preview_import: async () => answer("preview_import"),
    confirm_import: async () => answer("confirm_import"),
    refresh: async () => answer("refresh"),
    analyze_mock: async () => answer("analyze_mock"),
    follow_up: async () => answer("follow_up"),
    view: async () => answer("view"),
    export_report: async () => new Uint8Array(),
    delete_project: async () => answer("delete_project"),
  };
  return checkedPort(base);
}

test("B1 the shared fixtures pass, so the checks describe the real contract", () => {
  assert.equal(checkProductView(copy(DEMO)).schema_version, PRODUCT_VIEW_SCHEMA);
  assert.ok(checkPreview(read("p6_preview_import_v2.json")).preview_id);
});

test("B2 an unsupported schema version is refused, whatever the rest looks like", () => {
  for (const version of ["p6-product-view/v2", "p6-product-view/v999", "", null, undefined]) {
    assert.throws(() => checkProductView({ ...copy(DEMO), schema_version: version }), coded("schema_unsupported"));
  }
});

test("B3 a missing or wrong field is refused rather than dereferenced later", () => {
  const breaks = [
    v => { delete v.result; },
    v => { delete v.claims; },
    v => { v.claims[0].citation_ids = "demo-evidence-metric"; },
    v => { v.evidence[0].redacted = "no"; },
    v => { v.snapshot.metric_changes[0].delta = "-0.08"; },
    v => { v.snapshot.metric_changes[0].after.value = null; },
    v => { v.result.denominators.claims = "1"; },
    v => { v.graph.nodes[0].id = 7; },
    v => { v.conversation.turns[0].runtime = {}; },
  ];
  for (const change of breaks) {
    const v = copy(DEMO);
    change(v);
    assert.throws(() => checkProductView(v), coded("response_invalid"), String(change));
  }
});

test("B4 null stays legal where the contract says null: it is unknown, not malformed", () => {
  const v = copy(DEMO);
  v.result.denominators.independent_families = null;
  v.result.disposition = null;
  v.snapshot.metric_changes[0].delta = null;
  v.snapshot.metric_changes[0].before = null;
  assert.doesNotThrow(() => checkProductView(v));
});

test("B5 a citation that leads nowhere or a duplicate ID is refused", () => {
  const dangling = copy(DEMO);
  dangling.claims[0].citation_ids = ["evidence-that-is-not-here"];
  assert.throws(() => checkProductView(dangling), coded("response_invalid"));
  const duplicate = copy(DEMO);
  duplicate.evidence.push(copy(duplicate.evidence[0]));
  assert.throws(() => checkProductView(duplicate), coded("response_invalid"));
});

test("B6 an answer about another result, project or snapshot is out of scope", async () => {
  const other = stored();
  await assert.rejects(portAnswering(() => other).view("result-asked-for"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => other).analyze_mock("project-asked", other.snapshot.id, "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => other).analyze_mock(other.project.id, "snapshot-asked", "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => other).follow_up("parent-asked", "claim", "c", "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => ({ project_id: "other", snapshot_id: "s", status: "unchanged" })).refresh("asked"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => ({ project_id: "other", status: "deleted", deleted_count: 1, retained_shared_count: 0 })).delete_project("asked"), coded("response_out_of_scope"));
});

test("B7 the matching answer passes, and sample data can never answer for a stored result", async () => {
  const good = stored();
  assert.equal((await portAnswering(() => good).view(good.result.id)).result.id, good.result.id);
  assert.equal((await portAnswering(() => good).analyze_mock(good.project.id, good.snapshot.id, "Q")).result.id, good.result.id);
  assert.equal((await portAnswering(() => good).follow_up(good.result.id, "claim", "c", "Q")).result.id, good.result.id);
  await assert.rejects(portAnswering(() => copy(DEMO)).view(DEMO.result.id), coded("response_out_of_scope"));
  assert.equal((await portAnswering(() => copy(DEMO)).demo_view()).demo_only, true);
  await assert.rejects(portAnswering(() => stored()).demo_view(), coded("response_out_of_scope"));
});

test("B8 malformed import answers are refused", async () => {
  const preview = read("p6_preview_import_v2.json");
  await assert.rejects(portAnswering(() => ({ ...preview, included_count: "2" })).preview_import("/x"), coded("response_invalid"));
  await assert.rejects(portAnswering(() => ({ project_id: "p" })).confirm_import("p", {}), coded("response_invalid"));
  await assert.rejects(portAnswering(() => ({ project_id: "p", snapshot_id: "s", status: "maybe" })).refresh("p"), coded("response_invalid"));
});

test("F1 a ticket is valid only while its scope is on screen", () => {
  let hash = "#/investigation?project=a&snapshot=s1&result=r1";
  const fence = createFence(() => scopeFromHash(hash));
  const t = fence.ticket();
  assert.equal(fence.current(t), true);
  hash = "#/investigation?project=a&snapshot=s1&result=r1&claim=c&evidence=e";
  assert.equal(fence.current(t), true, "selection inside the same result keeps the scope");
  hash = "#/investigation?project=a&snapshot=s2&result=r1";
  assert.equal(fence.current(t), true, "snapshot moves forward inside one operation");
  hash = "#/connect?project=a&snapshot=s1&result=r1";
  assert.equal(fence.current(t), false, "leaving the screen ends the operation (the Connect probe)");
  hash = "#/investigation?project=a&snapshot=s1&result=r2";
  assert.equal(fence.current(t), false, "another result");
  hash = "#/investigation?project=b&snapshot=s1&result=r1";
  assert.equal(fence.current(t), false, "another project");
});

test("F2 advancing the fence ends everything in flight even if the URL looks the same", () => {
  const hash = "#/investigation?result=r1";
  const fence = createFence(() => scopeFromHash(hash));
  const t = fence.ticket();
  fence.advance();
  assert.equal(fence.current(t), false, "logout or account switch cannot be seen in the URL");
  assert.equal(fence.current(fence.ticket()), true);
});

test("F3 account and workspace are part of the scope, ready for hosted sessions", () => {
  const a = scopeFromHash("#/x?result=r", { account: "a", workspace: "w" });
  assert.equal(sameScope(a, scopeFromHash("#/x?result=r", { account: "b", workspace: "w" })), false);
  assert.equal(sameScope(a, scopeFromHash("#/x?result=r", { account: "a", workspace: "v" })), false);
  assert.equal(sameScope(a, scopeFromHash("#/x?result=r", { account: "a", workspace: "w" })), true);
});

function reply(body, status = 200) {
  const bytes = new TextEncoder().encode(JSON.stringify(body));
  return { ok: status < 300, status, json: async () => JSON.parse(new TextDecoder().decode(bytes)), arrayBuffer: async () => bytes.buffer };
}

test("R1 a lost mutation answer is outcome_unknown, and is sent exactly once", async () => {
  let calls = 0;
  const port = new LiveProductPort({ fetch: async () => { calls += 1; throw new TypeError("network"); }, delay: async () => {} });
  await assert.rejects(port.confirm_import("p", {}), coded("outcome_unknown"));
  await assert.rejects(port.follow_up("r", "claim", "c", "Q"), coded("outcome_unknown"));
  await assert.rejects(port.delete_project("p"), coded("outcome_unknown"));
  assert.equal(calls, 3, "no automatic retry of anything with an effect");
});

test("R2 reads retry a bounded number of times, then report the bridge", async () => {
  let calls = 0;
  const waits = [];
  const port = new LiveProductPort({ fetch: async () => { calls += 1; throw new TypeError("network"); }, delay: async a => { waits.push(a); } });
  await assert.rejects(port.view("r"), coded("bridge_unavailable"));
  assert.equal(calls, 3);
  assert.deepEqual(waits, [0, 1]);
});

test("R3 a refusal from the service is final: it is not retried", async () => {
  let calls = 0;
  const port = new LiveProductPort({ fetch: async () => { calls += 1; return reply({ code: "record_not_found", safe_message: "No." }, 422); }, delay: async () => {} });
  await assert.rejects(port.view("r"), coded("record_not_found"));
  assert.equal(calls, 1);
});

test("R4 identical reads in flight share one request; different ones do not", async () => {
  let calls = 0;
  let release;
  const gate = new Promise(r => { release = r; });
  const port = new LiveProductPort({ fetch: async () => { calls += 1; await gate; return reply({ ok: true }); } });
  const reads = [port.view("r"), port.view("r"), port.view("other")];
  release();
  await Promise.all(reads);
  assert.equal(calls, 2);
  await port.view("r");
  assert.equal(calls, 3, "a finished read is not cached");
});

test("R5 a read past its deadline aborts and says so", async () => {
  const port = new LiveProductPort({
    readDeadlineMs: 5,
    fetch: (_url, init) => new Promise((_resolve, reject) => { init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); }),
  });
  await assert.rejects(port.view("r"), coded("deadline_exceeded"));
});

test("R6 a mutation past its deadline is unknown, not failed", async () => {
  const port = new LiveProductPort({
    mutationDeadlineMs: 5,
    fetch: (_url, init) => new Promise((_resolve, reject) => { init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); }),
  });
  await assert.rejects(port.refresh("p"), coded("outcome_unknown"));
});
