/* The request lifecycle and what stays in the browser (Q-06.03, Q-06.04), graphs that loop (Q-10.04),
   and numbers and text the screens must not change (Q-08.02, Q-14.05). Every test runs the real code.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { LiveProductPort } from "../src/service/live.ts";
import { createFence, createTracker, scopeFromHash } from "../src/service/fence.ts";
import { resultTransitions } from "../src/service/transitions.ts";
import { claimPath } from "../src/data/subgraph.ts";
import { lineageLayout } from "../src/data/lineage.ts";
import { definitionWords, formatNumber, formatSigned } from "../src/data/format.ts";
import { canonicalQuestion, questionProblem, QUESTION_MAX } from "../src/service/question.ts";

const coded = code => e => e.name === "ProductError" && e.code === code;
const never = () => new Promise(() => {});
const stalled = () => ({ ok: true, status: 200, json: never, arrayBuffer: never });
const live = (fetch, more = {}) => new LiveProductPort({ fetch, delay: async () => {}, ...more });

test("Q1 a body that stalls after the headers ends at the deadline, as a read or as a mutation", async () => {
  const quick = { readDeadlineMs: 20, mutationDeadlineMs: 20 };
  await assert.rejects(live(async () => stalled(), quick).view("r"), coded("deadline_exceeded"));
  await assert.rejects(live(async () => stalled(), quick).export_report("r", "pdf"), coded("deadline_exceeded"));
  await assert.rejects(live(async () => stalled(), quick).confirm_import("p", {}), coded("outcome_unknown"), "the service may have done it");
  const errorBody = { ok: false, status: 500, json: never, arrayBuffer: never };
  await assert.rejects(live(async () => errorBody, quick).view("r"), coded("service_failure"), "a stalled error body is not waited on forever");
});

test("Q2 a stalled read does not block the next one for the same result", async () => {
  let calls = 0;
  const port = live(async () => { calls += 1; return calls === 1 ? stalled() : { ok: true, status: 200, json: async () => ({ ok: 1 }) }; }, { readDeadlineMs: 20 });
  await assert.rejects(port.view("r"), coded("deadline_exceeded"));
  assert.deepEqual(await port.view("r"), { ok: 1 });
});

test("Q3 moving on stops a view read; a mutation in flight is never abandoned", async () => {
  const signals = {};
  const fetch = async (url, init) => { signals[url.split("/").pop()] = init.signal; return never(); };
  const port = live(fetch, { mutationDeadlineMs: 50 });
  const view = port.view("r");
  const confirm = port.confirm_import("p", {});
  await new Promise(r => setTimeout(r, 0));
  port.abandonViews();
  await assert.rejects(view, coded("deadline_exceeded"));
  assert.equal(signals.view.aborted, true);
  assert.equal(signals.confirm_import.aborted, false);
  await assert.rejects(confirm, coded("outcome_unknown"), "only its own deadline ends it");
});

test("Q4 a gateway's own 5xx on a mutation is 'outcome unknown', never 'nothing changed'", async () => {
  const page = status => async () => ({ ok: false, status, json: async () => { throw new Error("html"); } });
  for (const status of [500, 502, 504]) {
    await assert.rejects(live(page(status)).confirm_import("p", {}), coded("outcome_unknown"), String(status));
    await assert.rejects(live(page(status)).delete_project("p"), coded("outcome_unknown"), String(status));
  }
  // the bridge's own refusal is a real answer, and a read changed nothing
  const bridge = async () => ({ ok: false, status: 500, json: async () => ({ code: "import_failed", safe_message: "No." }) });
  await assert.rejects(live(bridge).confirm_import("p", {}), coded("import_failed"));
  await assert.rejects(live(page(502)).view("r"), coded("service_failure"));
});

test("Q5 a second press while the first runs is not sent, and does not stay busy", async () => {
  const tracker = createTracker(createFence(() => scopeFromHash("#/investigation")));
  let sends = 0, release;
  const work = () => { sends += 1; return new Promise(r => { release = r; }); };
  const first = tracker.run("follow_up", work, { key: "k", done() {}, fail() {} });
  let busy = true;
  await tracker.run("follow_up", work, { key: "k", done() {}, fail() {}, always: () => { busy = false; } });
  assert.equal(sends, 1);
  assert.equal(busy, false, "the second caller's busy flag is released");
  release("x");
  await first;
});

test("Q6 ending a session forgets running work too: its question never reaches the recent list", async () => {
  const fence = createFence(() => scopeFromHash("#/investigation?result=r"));
  const tracker = createTracker(fence);
  let release, fail;
  const ok = tracker.run("follow_up", () => new Promise(r => { release = r; }), { label: "Why did accuracy drop for client ACME?", key: "k", done() {}, fail() {}, late: () => ({ project_id: "p", snapshot_id: "s", result_id: "r", question: "Why did accuracy drop for client ACME?" }) });
  const bad = tracker.run("analyze", () => new Promise((_, no) => { fail = no; }), { done() {}, fail() {} });
  assert.equal(tracker.running().length, 2);
  fence.advance(); tracker.clear();
  assert.deepEqual(tracker.running(), [], "nothing about the old scope stays in memory");
  release("x"); fail(new Error("late"));
  await ok; await bad;
  assert.deepEqual(tracker.late(), [], "and nothing is filed when it ends");
  let sends = 0;
  await tracker.run("follow_up", async () => { sends += 1; }, { key: "k", done() {}, fail() {} });
  assert.equal(sends, 1, "the same key can be sent again in the new session");
});

test("Q7 a superseded view read is abandoned; the one still wanted is not", async () => {
  let abandoned = 0;
  const seen = [];
  const t = resultTransitions({ read: async id => ({ id }), identify: v => v.id, accept: v => seen.push(v.id), reject() {}, abandon: () => { abandoned += 1; } });
  await t.load("a");
  const before = abandoned;
  await t.navigate("a");
  assert.equal(abandoned, before, "same result: its read is left alone");
  await t.navigate("b");
  assert.equal(abandoned, before + 1);
  t.cancel();
  assert.equal(abandoned, before + 2);
  assert.deepEqual(seen, ["a", "b"]);
});

test("Q8 what is kept in the browser has a version; an unknown one is not read or guessed at", async () => {
  const mem = new Map();
  globalThis.localStorage = { getItem: k => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: k => mem.delete(k) };
  const { openedProject, rememberProject } = await import("../src/service/opened.ts");
  const ids = { project_id: "p3-project-abc", snapshot_id: "p3-snapshot-def" };
  rememberProject(ids);
  assert.equal(JSON.parse(mem.get("p6.opened")).v, 1);
  assert.deepEqual(openedProject(), ids);
  mem.set("p6.opened", JSON.stringify(ids));
  assert.deepEqual(openedProject(), ids, "an entry from before versions has the same fields and is still read");
  for (const v of [2, 0, "1", null]) {
    mem.set("p6.opened", JSON.stringify({ v, ...ids }));
    assert.equal(openedProject(), null, `version ${JSON.stringify(v)}`);
  }
  rememberProject({ ...ids, token: "secret", bearer: "abc" });
  assert.deepEqual(Object.keys(JSON.parse(mem.get("p6.opened"))).sort(), ["project_id", "snapshot_id", "v"], "no field a caller adds is stored");
  delete globalThis.localStorage;
});

const node = (id, kind) => ({ id, kind, source_id: id, visibility: "visible" });
const edge = (id, source, target, kind = "CITES") => ({ id, kind, source, target, visibility: "visible" });

test("Q9 a graph with a cycle or a self-loop lays out and walks to an end (Q-10.04)", () => {
  const nodes = [node("e1", "EvidenceItem"), node("e2", "EvidenceItem"), node("c1", "AtomicClaim"), node("c2", "AtomicClaim"), node("s", "Snapshot")];
  const edges = [edge("a", "c1", "e1"), edge("b", "e1", "c1"), edge("c", "c1", "c2"), edge("d", "c2", "c1"), edge("self", "e2", "e2"), edge("o", "e1", "s", "OBSERVED_IN")];
  const layout = lineageLayout(nodes, edges);
  assert.deepEqual(layout.dangling, []);
  assert.equal(layout.edges.length, edges.length, "every edge stays listed, none repaired away");
  const path = claimPath(nodes, edges, "c1");
  assert.deepEqual(path.nodes.map(n => n.id).sort(), ["c1", "c2", "e1", "s"]);
  assert.deepEqual(path.edges.map(e => e.id).sort(), ["a", "b", "c", "d", "o"]);
});

test("Q10 a cycle over 2,000 nodes finishes quickly and in the same order whatever order it arrives in", () => {
  const nodes = Array.from({ length: 2000 }, (_, i) => node(`c${i}`, "AtomicClaim"));
  const edges = nodes.map((n, i) => edge(`e${i}`, n.id, nodes[(i + 1) % nodes.length].id));
  const t0 = performance.now();
  const a = lineageLayout(nodes, edges), b = lineageLayout([...nodes].reverse(), [...edges].reverse());
  claimPath(nodes, edges, "c0");
  assert.ok(performance.now() - t0 < 1000);
  assert.deepEqual(a.lanes, b.lanes);
  assert.deepEqual(a.edges, b.edges);
});

test("Q11 zero, negatives, tiny and huge values keep their value and sign (Q-08.02)", () => {
  assert.equal(formatNumber(0, "en-US"), "0");
  assert.equal(formatNumber(-0, "en-US"), "0", "-0 is not a direction");
  assert.equal(formatSigned(-0, "en-US"), "0");
  assert.equal(formatSigned(0, "en-US"), "0");
  assert.equal(formatSigned(-0.08, "vi-VN"), "-0,08");
  assert.equal(formatSigned(0.08, "en-US"), "+0.08");
  assert.equal(formatNumber(1e-7, "en-US"), "1e-7", "never rounded to 0");
  assert.equal(formatNumber(0.1 + 0.2, "en-US"), "0.30000000000000004", "no digit dropped, so no threshold can be crossed by rounding");
  assert.equal(formatNumber(1.5e15, "en-US"), "1500000000000000");
});

test("Q12 a direction is read as written; one this client does not know is not reversed (Q-08.02)", () => {
  assert.match(definitionWords({ metric_name: "m", direction: "higher_is_better", regression_threshold: 0.08 }, "en-US"), /^Higher is better; threshold 0\.08/);
  assert.match(definitionWords({ metric_name: "m", direction: "lower_is_better", regression_threshold: 0 }, "en-US"), /^Lower is better; threshold 0/);
  assert.doesNotMatch(definitionWords({ metric_name: "m", direction: "higher-is-better", regression_threshold: 1 }, "en-US"), /Lower is better/);
});

test("Q13 Vietnamese typed in either Unicode form is one question, counted in characters (Q-14.05)", () => {
  const nfd = "Độ chính xác giảm vì sao?".normalize("NFD");
  const nfc = "Độ chính xác giảm vì sao?".normalize("NFC");
  assert.notEqual(nfd, nfc);
  assert.equal(canonicalQuestion(nfd), nfc);
  assert.equal(questionProblem(canonicalQuestion("é".normalize("NFD").repeat(QUESTION_MAX))), null, "2000 letters, not 4000 code points");
});
