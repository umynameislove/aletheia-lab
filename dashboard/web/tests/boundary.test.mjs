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
    analyze_observations: async () => answer("analyze_observations"),
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
  for (const version of ["p6-product-view/v3", "p6-product-view/v999", "", null, undefined]) {
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
  await assert.rejects(portAnswering(() => other).analyze_observations("project-asked", other.snapshot.id, "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => other).analyze_observations(other.project.id, "snapshot-asked", "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => other).follow_up("parent-asked", "claim", "c", "Q"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => ({ project_id: "other", snapshot_id: "s", status: "unchanged" })).refresh("asked"), coded("response_out_of_scope"));
  await assert.rejects(portAnswering(() => ({ project_id: "other", status: "deleted", deleted_count: 1, retained_shared_count: 0 })).delete_project("asked"), coded("response_out_of_scope"));
});

test("B7 the matching answer passes, and sample data can never answer for a stored result", async () => {
  const good = stored();
  assert.equal((await portAnswering(() => good).view(good.result.id)).result.id, good.result.id);
  assert.equal((await portAnswering(() => good).analyze_mock(good.project.id, good.snapshot.id, "Q")).result.id, good.result.id);
  assert.equal((await portAnswering(() => good).analyze_observations(good.project.id, good.snapshot.id, "Q")).result.id, good.result.id);
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

/** A 2xx whose body breaks off part way: the JSON cannot be parsed, or the stream itself fails. */
function cutOff(body, how) {
  const text = JSON.stringify(body);
  const fail = how === "stream" ? () => Promise.reject(new TypeError("network error")) : null;
  return {
    ok: true, status: 200,
    json: fail ?? (async () => JSON.parse(text.slice(0, Math.floor(text.length / 2)))),
    arrayBuffer: fail ?? (async () => new TextEncoder().encode(text).buffer),
  };
}

test("R7 a read whose answer is cut off is unreadable, and nothing is shown from it", async () => {
  for (const how of ["parse", "stream"]) {
    let calls = 0;
    const port = new LiveProductPort({ fetch: async () => { calls += 1; return cutOff(DEMO, how); }, delay: async () => {} });
    await assert.rejects(port.view("r"), coded("service_failure"));
    assert.equal(calls, 1, "the service answered, so the bridge is not retried");
  }
});

test("R8 a mutation the service accepted but whose answer is cut off is unknown, not failed", async () => {
  for (const how of ["parse", "stream"]) {
    let calls = 0;
    const port = new LiveProductPort({ fetch: async () => { calls += 1; return cutOff({ deleted_count: 3 }, how); } });
    await assert.rejects(port.delete_project("p"), coded("outcome_unknown"));
    await assert.rejects(port.confirm_import("p", {}), coded("outcome_unknown"));
    await assert.rejects(port.follow_up("r", "claim", "c", "Q"), coded("outcome_unknown"));
    await assert.rejects(port.analyze_observations("p", "s", "Q"), coded("outcome_unknown"));
    assert.equal(calls, 4, "and none of them is sent again");
  }
});

test("R9 a report whose bytes break off is refused, never saved as a whole file", async () => {
  const port = new LiveProductPort({ fetch: async () => cutOff({}, "stream") });
  await assert.rejects(port.export_report("r", "pdf"), coded("service_failure"));
});

test("R10 a cut-off view that still parses is caught by the contract check", async () => {
  // a proxy that closes the body on a boundary can leave valid JSON with half the fields missing
  const half = Object.fromEntries(Object.entries(stored()).slice(0, 3));
  const port = portAnswering(() => half);
  await assert.rejects(port.view(DEMO.result.id), e => e.name === "ProductError" && ["response_invalid", "schema_unsupported"].includes(e.code));
});

/* ---- the tracker: double sends, late outcomes and what stays visible ---- */
import { createTracker } from "../src/service/fence.ts";

function tracked() {
  const state = { hash: "#/investigation?result=r1" };
  const fence = createFence(() => scopeFromHash(state.hash));
  const tracker = createTracker(fence);
  return { state, fence, tracker };
}
const gated = () => { let release, fail; const p = new Promise((yes, no) => { release = yes; fail = no; }); return { p, release, fail }; };

test("T1 the same key in flight is not sent twice, and can be sent again once finished", async () => {
  const { tracker } = tracked();
  let sends = 0;
  const g = gated();
  const work = () => { sends += 1; return g.p; };
  const then = { key: "k", done() {}, fail() {} };
  const first = tracker.run("follow_up", work, then);
  await tracker.run("follow_up", work, then);
  assert.equal(sends, 1, "a double click sends once");
  g.release("x");
  await first;
  await tracker.run("follow_up", async () => { sends += 1; }, then);
  assert.equal(sends, 2);
});

test("T2 a success that arrives after moving on changes nothing and can be found later", async () => {
  const { state, tracker } = tracked();
  const g = gated();
  let shown = 0;
  const running = tracker.run("analyze", () => g.p, { done() { shown += 1; }, fail() {}, label: "Q", late: v => ({ project_id: "p", snapshot_id: "s", result_id: v, question: "Q" }) });
  assert.deepEqual(tracker.running().map(w => w.label), ["Q"], "running work is visible");
  state.hash = "#/connect?result=r1";
  g.release("r2");
  await running;
  assert.equal(shown, 0);
  assert.deepEqual(tracker.running(), []);
  assert.equal(tracker.late()[0].result.result_id, "r2");
  tracker.dismiss(tracker.late()[0].id);
  assert.deepEqual(tracker.late(), []);
});

test("T3 an error that arrives after moving on shows no toast, and is recorded by code only", async () => {
  const { state, tracker } = tracked();
  const g = gated();
  let failures = 0;
  const running = tracker.run("refresh", () => g.p, { done() {}, fail() { failures += 1; }, codeOf: e => e.code });
  state.hash = "#/investigation?result=r2";
  g.fail({ code: "refresh_failed", safe_message: "private detail" });
  await running;
  assert.equal(failures, 0);
  assert.deepEqual(tracker.late().map(w => [w.op, w.error, w.result]), [["refresh", "refresh_failed", undefined]]);
});

test("T4 the current screen gets its own outcome directly, and the late list stays empty", async () => {
  const { tracker } = tracked();
  const seen = [];
  await tracker.run("analyze", async () => "ok", { done: v => seen.push(v), fail() {} });
  await tracker.run("analyze", async () => { throw new Error("no"); }, { done() {}, fail: e => seen.push(e.message) });
  assert.deepEqual(seen, ["ok", "no"]);
  assert.deepEqual(tracker.late(), []);
});

test("T5 logout or account switch forgets late work", async () => {
  const { state, fence, tracker } = tracked();
  const g = gated();
  const running = tracker.run("analyze", () => g.p, { done() {}, fail() {} });
  fence.advance();
  void state;
  g.release("x");
  await running;
  assert.equal(tracker.late().length, 1);
  tracker.clear();
  assert.deepEqual(tracker.late(), []);
});

test("S1 a proxy's status still says what to do next, and a service error body wins over it", async () => {
  // 401 and 403 alone cannot tell expired from revoked from refused, so they are not guessed (J-01 review v2)
  for (const [status, code] of [[401, "access_unconfirmed"], [403, "access_unconfirmed"], [404, "not_found"], [409, "revision_conflict"], [413, "payload_too_large"], [429, "rate_limited"], [503, "service_busy"]]) {
    const port = new LiveProductPort({ fetch: async () => ({ ok: false, status, json: async () => { throw new Error("html"); }, arrayBuffer: async () => new ArrayBuffer(0) }), delay: async () => {} });
    await assert.rejects(port.view("r"), coded(code), String(status));
  }
  const port = new LiveProductPort({ fetch: async () => reply({ code: "backend_unavailable", safe_message: "Not installed." }, 503), delay: async () => {} });
  await assert.rejects(port.view("r"), coded("backend_unavailable"));
});

test("S2 a status with no fixed meaning stays generic", async () => {
  const port = new LiveProductPort({ fetch: async () => reply({ nothing: true }, 500), delay: async () => {} });
  await assert.rejects(port.view("r"), coded("service_failure"));
});
