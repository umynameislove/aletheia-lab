/* The P1 fixes from the 2026-11-08 production audit (04-Kiem-dinh-08-11), checked by running the code,
   not by reading it. What needs a browser (focus, keys, dialogs) is in tests/e2e/p1.e2e.mjs.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { metricNameProblem, parseThreshold } from "../src/data/metric.ts";
import { fresh, reduce, reviewOf } from "../src/state.ts";
import { reviewerPass } from "../src/export.ts";
import { keptOnArrival } from "../src/service/opened.ts";
import { createFence, scopeFromHash, createTracker } from "../src/service/fence.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("P1-01 a threshold typed with a decimal comma is the same number as with a point (KD-02)", () => {
  for (const [typed, sent] of [["0,05", 0.05], ["0.05", 0.05], [" 0,2 ", 0.2], [",5", 0.5], ["1,", 1], ["2,5e-4", 0.00025], ["0", 0]]) {
    assert.equal(parseThreshold(typed), sent, typed);
  }
  // one separator at most, never a sign, never hex or words: the backend's rule otherwise unchanged
  for (const typed of ["1.000,5", "1,000.5", "0,0,5", "-0,1", "abc", "", "1e999", "0x10", "Infinity", ",", "."]) {
    assert.equal(parseThreshold(typed), null, typed);
  }
});

test("P1-02 a typed metric name is checked against the service's own identifier rule (KD-01)", () => {
  const py = readFileSync(join(ROOT, "../../src/aletheia_lab/product/candidates.py"), "utf8");
  const m = /_IDENTIFIER[^=]*=\s*re\.compile\(r"(.+?)"\)/.exec(py);
  assert.ok(m, "the service's _IDENTIFIER pattern was not found");
  const service = new RegExp(m[1]);
  const samples = ["loss", "val_loss", "top-1", "acc@5", "eval:f1", "a.b", "độ_chính_xác", "Accuracy", "_x", "-x", "x y", "a".repeat(128), "a".repeat(129), "😀", "1st"];
  for (const name of samples) {
    assert.equal(metricNameProblem(name) === null, service.test(name), `client and service disagree on ${JSON.stringify(name)}`);
  }
  assert.match(metricNameProblem("độ_chính_xác"), /cannot be imported yet/);
  assert.equal(metricNameProblem("  "), null, "an empty name is the other check's to report");
});

test("P1-03 unticking a disputed claim keeps the objection; only Reset review clears it (KD-05)", () => {
  let s = fresh();
  s = reduce(s, { type: "review", id: "c1", value: "disputed" });
  s = reduce(s, { type: "note", id: "c1", text: "The source is from the wrong run." });
  s = reduce(s, { type: "review", id: "c1", value: "unchecked" });
  assert.equal(reviewOf(s, "c1"), "unchecked");
  assert.equal(s.notes.c1, "The source is from the wrong run.", "the objection survives the mark");
  s = reduce(s, { type: "review", id: "c1", value: "checked" });
  s = reduce(s, { type: "review", id: "c1", value: "disputed" });
  assert.equal(s.notes.c1, "The source is from the wrong run.", "disputing again brings it back");
  s = reduce(s, { type: "reviewReset" });
  assert.deepEqual([s.review, s.notes], [{}, {}]);
});

test("P1-04 the review file carries an objection only for a claim that is disputed (KD-05)", () => {
  const v = { claims: [{ id: "c1" }, { id: "c2" }] };
  let s = fresh();
  s = reduce(s, { type: "review", id: "c1", value: "disputed" });
  s = reduce(s, { type: "note", id: "c1", text: "wrong run" });
  s = reduce(s, { type: "review", id: "c2", value: "disputed" });
  s = reduce(s, { type: "note", id: "c2", text: "kept but not disputed now" });
  s = reduce(s, { type: "review", id: "c2", value: "checked" });
  const pass = reviewerPass(v, s);
  assert.deepEqual(pass.claims.c1, { state: "disputed", objection: "wrong run" });
  assert.deepEqual(pass.claims.c2, { state: "checked", objection: null });
});

test("P1-05 an import confirmed after the person left Connect is still kept for Start (KD-06)", async () => {
  const state = { hash: "#/connect" };
  const tracker = createTracker(createFence(() => scopeFromHash(state.hash)));
  const kept = [];
  let answer;
  const confirm = new Promise(r => { answer = r; });
  let shown = false;
  const run = tracker.run("confirm", () => keptOnArrival(confirm, o => kept.push(o)), { done: () => { shown = true; }, fail: () => {} });
  state.hash = "#/results"; // the person moves on while the service is still confirming
  answer({ project_id: "p3-project-a", snapshot_id: "p3-snapshot-b", status: "created" });
  await run;
  assert.equal(shown, false, "the Connect screen is not updated from another screen");
  assert.deepEqual(kept, [{ project_id: "p3-project-a", snapshot_id: "p3-snapshot-b" }], "but the project is kept");
  assert.equal(tracker.late()[0].op, "confirm", "and the finished import is listed");
});

test("P1-06 a refused confirm keeps nothing (KD-06)", async () => {
  const kept = [];
  await assert.rejects(keptOnArrival(Promise.reject(new Error("mapping_invalid")), o => kept.push(o)));
  assert.deepEqual(kept, []);
});
