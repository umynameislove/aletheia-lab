/* LiveProductPort over a recorded fetch: the request each method sends, how answers and refusals
   come back, and parity with the Python bridge's METHOD_PARAMETERS.   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { BRIDGE_HEADER, LiveProductPort } from "../src/service/live.ts";
import { activateProject, projectDeleted, resultTransitions } from "../src/service/transitions.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BRIDGE = readFileSync(join(ROOT, "../../src/aletheia_lab/dashboard/bridge.py"), "utf8");

function recorder(answer = () => ({ status: 200, body: { ok: true } })) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init, args: JSON.parse(init.body) });
    const a = answer(url, init);
    if (a instanceof Error) throw a;
    const bytes = a.bytes ?? new TextEncoder().encode(typeof a.body === "string" ? a.body : JSON.stringify(a.body));
    return {
      ok: a.status >= 200 && a.status < 300,
      status: a.status,
      json: async () => JSON.parse(new TextDecoder().decode(bytes)),
      arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    };
  };
  return { calls, port: new LiveProductPort({ fetch }) };
}

const CALLS = {
  demo_view: [],
  preview_import: ["/tmp/project"],
  confirm_import: ["preview-1", { baseline_run_id: "baseline" }],
  refresh: ["project-1"],
  analyze_mock: ["project-1", "snapshot-1", "Why?"],
  follow_up: ["result-1", "claim", "claim-1", "And?"],
  view: ["result-1"],
  export_report: ["result-1", "pdf"],
  delete_project: ["project-1"],
};

/** METHOD_PARAMETERS as written in bridge.py: { method: [names…] } */
function bridgeParameters() {
  const block = BRIDGE.slice(BRIDGE.indexOf("METHOD_PARAMETERS"), BRIDGE.indexOf("}", BRIDGE.indexOf("METHOD_PARAMETERS")));
  const out = {};
  for (const m of block.matchAll(/"([a-z_]+)":\s*\(([^)]*)\)/g)) out[m[1]] = [...m[2].matchAll(/"([a-z_]+)"/g)].map(x => x[1]);
  return out;
}

test("L1 every method posts its exact arguments to /api/<method> with the bridge header", async () => {
  const params = bridgeParameters();
  assert.deepEqual(Object.keys(params).sort(), Object.keys(CALLS).sort(), "nine methods on both sides");
  for (const [method, args] of Object.entries(CALLS)) {
    const { calls, port } = recorder(() => ({ status: 200, body: {}, bytes: method === "export_report" ? new Uint8Array([37, 80, 68, 70]) : undefined }));
    await port[method](...args);
    assert.equal(calls.length, 1);
    const [{ url, init, args: sent }] = calls;
    assert.equal(url, `/api/${method}`);
    assert.equal(init.method, "POST");
    assert.equal(init.headers[BRIDGE_HEADER], "1");
    assert.equal(init.headers["Content-Type"], "application/json");
    assert.deepEqual(Object.keys(sent), params[method], `${method} sends the bridge's parameter names, in order`);
    assert.deepEqual(Object.values(sent), args);
  }
});

test("L2 the folder path travels in the body, never in the URL", async () => {
  const { calls, port } = recorder();
  await port.preview_import("/Users/someone/secret-project");
  assert.ok(!calls[0].url.includes("secret-project"));
  assert.equal(calls[0].args.root, "/Users/someone/secret-project");
});

test("L3 a refusal comes back as the service's ProductError", async () => {
  const { port } = recorder(() => ({ status: 422, body: { code: "record_not_found", safe_message: "The result was not found." } }));
  await assert.rejects(port.view("result-1"), e => e.name === "ProductError" && e.code === "record_not_found" && e.safe_message === "The result was not found.");
});

test("L4 anything that is not a well-formed {code, safe_message} becomes a generic code", async () => {
  for (const body of ["<html>proxy error</html>", { code: "Not A Code", safe_message: "x" }, { error: "boom" }, { code: "x", safe_message: "y".repeat(300) }]) {
    const { port } = recorder(() => ({ status: 500, body }));
    await assert.rejects(port.view("result-1"), e => e.name === "ProductError" && e.code === "service_failure");
  }
});

test("L5 an unreachable bridge is reported, never replaced by demo data", async () => {
  const { port } = recorder(() => new TypeError("Failed to fetch"));
  assert.equal(port.kind, "live");
  await assert.rejects(port.demo_view(), e => e.code === "bridge_unavailable");
});

test("L6 export_report returns the response bytes untouched", async () => {
  const bytes = new Uint8Array([37, 80, 68, 70, 45, 0, 255, 10]);
  const { port } = recorder(() => ({ status: 200, bytes }));
  assert.deepEqual(await port.export_report("result-1", "pdf"), bytes);
});

test("L7 the remedy table covers the bridge's own codes", async () => {
  const { KNOWN_CODES } = await import("../src/service/errors.ts");
  for (const code of [...BRIDGE.matchAll(/BridgeError\(\s*"([a-z_]+)"/g)].map(m => m[1]).filter(c => c !== "not_found")) {
    assert.ok(KNOWN_CODES.includes(code), `${code} has a remedy`);
  }
  for (const code of ["bridge_unavailable", "service_failure"]) assert.ok(KNOWN_CODES.includes(code));
});

test("L8 the kept project holds opaque IDs only and survives bad storage", async () => {
  const mem = new Map();
  globalThis.localStorage = { getItem: k => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: k => mem.delete(k) };
  const { openedProject, rememberProject, forgetProject } = await import("../src/service/opened.ts");
  rememberProject({ project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def", result_id: "p6-result-123", root: "/Users/x/secret", mapping: { a: 1 } });
  assert.deepEqual(JSON.parse(mem.get("p6.opened")), { project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def", result_id: "p6-result-123" }, "nothing but the three IDs is stored");
  assert.deepEqual(openedProject(), { project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def", result_id: "p6-result-123" });
  rememberProject({ project_id: "/Users/x/secret", snapshot_id: "s" });
  assert.equal(openedProject().project_id, "p3-project-abc", "a path is never accepted as an ID");
  mem.set("p6.opened", "{not json");
  assert.equal(openedProject(), null);
  rememberProject({ project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def" });
  forgetProject("p3-project-other");
  assert.ok(openedProject(), "forgetting another project keeps this one");
  forgetProject("p3-project-abc");
  assert.equal(openedProject(), null);
  globalThis.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(openedProject(), null);
  rememberProject({ project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def" });
  delete globalThis.localStorage;
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function transitionHarness() {
  const calls = [], accepted = [], rejected = [];
  const requests = resultTransitions({
    read(id) { const d = deferred(); calls.push({ id, ...d }); return d.promise; },
    identify: view => view.id,
    accept: view => accepted.push(view.id),
    reject: error => rejected.push(error.message),
  });
  return { requests, calls, accepted, rejected };
}

test("L9 a late initial success or error cannot replace a newer navigation", async () => {
  for (const fails of [false, true]) {
    const h = transitionHarness();
    const initial = h.requests.load("a");
    const next = h.requests.navigate("b");
    h.calls[1].resolve({ id: "b" }); await next;
    if (fails) h.calls[0].reject(new Error("stale initial error"));
    else h.calls[0].resolve({ id: "a" });
    await initial;
    assert.deepEqual(h.accepted, ["b"]);
    assert.deepEqual(h.rejected, []);
  }
});

test("L10 Back to the displayed result cancels pending success and error", async () => {
  for (const fails of [false, true]) {
    const h = transitionHarness();
    h.requests.show({ id: "a" });
    const pending = h.requests.navigate("b");
    await h.requests.navigate("a");
    if (fails) h.calls[0].reject(new Error("stale navigation error"));
    else h.calls[0].resolve({ id: "b" });
    await pending;
    assert.deepEqual(h.accepted, ["a", "a"]);
    assert.equal(h.calls.length, 1, "Back uses the displayed immutable result");
    assert.deepEqual(h.rejected, []);
  }
});

test("L11 direct analysis/follow-up results invalidate pending success and error", async () => {
  for (const fails of [false, true]) {
    const h = transitionHarness();
    const pending = h.requests.load("old");
    h.requests.show({ id: "child" });
    await h.requests.navigate("child"); // hashchange after showResult must not refetch
    if (fails) h.calls[0].reject(new Error("stale error"));
    else h.calls[0].resolve({ id: "old" });
    await pending;
    assert.deepEqual(h.accepted, ["child"]);
    assert.deepEqual(h.rejected, []);
    assert.equal(h.calls.length, 1);
  }
});

test("L12 clearing the result or unmounting invalidates in-flight work", async () => {
  for (const cancel of [h => h.requests.navigate(null), h => h.requests.cancel()]) {
    const h = transitionHarness();
    const pending = h.requests.load("old");
    await cancel(h);
    h.calls[0].resolve({ id: "old" }); await pending;
    assert.deepEqual(h.accepted, []);
  }
});

test("L13 current errors surface and Back restores the displayed view", async () => {
  const h = transitionHarness();
  h.requests.show({ id: "a" });
  const pending = h.requests.navigate("b");
  h.calls[0].reject(new Error("current error")); await pending;
  assert.deepEqual(h.rejected, ["current error"]);
  await h.requests.navigate("a");
  assert.deepEqual(h.accepted, ["a", "a"]);
});

test("L14 selection changes on a pending result do not restart the request", async () => {
  const h = transitionHarness();
  const pending = h.requests.load("a");
  await h.requests.navigate("a");
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({ id: "a" }); await pending;
  assert.deepEqual(h.accepted, ["a"]);
});

test("L15 deletion state closes only its own project, including late responses", () => {
  const fresh = { deletedConversation: false, deletedProject: false, receipt: null, review: { claim: "checked" } };
  const receipt = { project_id: "a", status: "deleted", deleted_count: 2, retained_shared_count: 0, scope: "" };
  const a = activateProject(fresh, "a");
  const deleted = projectDeleted(a, receipt);
  assert.equal(deleted.deletedProject, true);
  assert.equal(activateProject(deleted, "a"), deleted, "same-project views do not undo deletion");
  const b = activateProject(deleted, "b");
  assert.equal(b.deletedProject, false);
  assert.equal(b.deletedConversation, false);
  assert.equal(b.receipt, null);
  assert.deepEqual(b.review, fresh.review, "unrelated reviewer annotations are preserved");
  assert.equal(projectDeleted(b, receipt), b, "a late response for A cannot close B");
});
