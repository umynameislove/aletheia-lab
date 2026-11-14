/* The P1 fixes from the 2026-11-08 production audit (04-Kiem-dinh-08-11), checked by running the code,
   not by reading it. What needs a browser (focus, keys, dialogs) is in tests/e2e/p1.e2e.mjs.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isMetricLabel, metricNameProblem, parseThreshold } from "../src/data/metric.ts";
import { fresh, reduce, reviewOf } from "../src/state.ts";
import { reviewerPass } from "../src/export.ts";
import { keptOnArrival } from "../src/service/opened.ts";
import { createFence, scopeFromHash, createTracker } from "../src/service/fence.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const VENV_PYTHON = join(ROOT, process.platform === "win32" ? "../../.venv/Scripts/python.exe" : "../../.venv/bin/python");
const PYTHON = process.env.PYTHON ?? (existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === "win32" ? "python" : "python3");

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
  const samples = ["loss", "val_loss", "top-1", "acc@5", "eval:f1", "a.b", "độ_chính_xác", "lần-1", "Accuracy", "_x", "-x", "x y", "a".repeat(128), "a".repeat(129), "😀", "1st", "e\u0301", "x\u200dy", "𐐀".repeat(128), "𐐀".repeat(129), "pa\u034fssword", "pa\ufe0fssword", "x\u115fy", "x\u{e0100}y", "\u{11f02}", "b\u0301\u{11f41}", "\u{11380}", "\u{1c89}", "\u{10d40}"];
  const checked = spawnSync(PYTHON, ["-B", "-c", `
import json, sys
from aletheia_lab.project.mapping import _identifier
answers = []
for name in json.load(sys.stdin):
    try:
        _identifier(name, label="metric_name")
        answers.append(True)
    except ValueError:
        answers.append(False)
print(json.dumps(answers))
`], { input: JSON.stringify(samples), encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1", PYTHONPATH: join(ROOT, "../../src") } });
  assert.equal(checked.status, 0, checked.stderr || String(checked.error));
  const generated = spawnSync(PYTHON,
    ["-B", join(ROOT, "../../scripts/export_mapping_identifiers.py"), "--check"],
    { encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1", PYTHONPATH: join(ROOT, "../../src") } });
  assert.equal(generated.status, 0, generated.stderr || String(generated.error));
  const service = JSON.parse(checked.stdout);
  samples.forEach((name, index) => {
    assert.equal(metricNameProblem(name) === null, service[index], `client and service disagree on ${JSON.stringify(name)}`);
  });
  assert.equal(metricNameProblem("độ_chính_xác"), null);
  assert.notEqual(metricNameProblem("\u{11f02}"), null, "the frozen Unicode14 policy rejects newer Kawi identifiers");
  assert.notEqual(metricNameProblem("b\u0301\u{11f41}"), null, "newer combining marks cannot cause cross-version NFC drift");
  assert.equal(metricNameProblem("  "), null, "an empty name is the other check's to report");
});

test("metric class/unit label hints match the real backend's canonical visible Unicode policy", () => {
  const samples = [["không bệnh", 128], ["%", 64], ["items/s", 64], ["kg / m²", 64], ["âm tính", 128],
    ["", 128], [" unit", 64], ["unit ", 64], ["e\u0301", 128], ["a\nunit", 64], ["a\tunit", 64],
    ["x\u034fy", 128], ["x\ufe0fy", 128], ["x\u200dy", 128], ["x\u202ey", 128], ["x\u2060y", 128],
    ["b\u0301\u{11f41}", 128], ["\u{11f02}", 128], ["𐐀".repeat(128), 128], ["𐐀".repeat(129), 128],
    ["u".repeat(64), 64], ["u".repeat(65), 64]];
  const checked = spawnSync(PYTHON, ["-B", "-c", `
import json, sys
from aletheia_lab.project.mapping_identifiers import metric_label
answers = []
for text, maximum in json.load(sys.stdin):
    try:
        metric_label(text, label="metric label", max_length=maximum)
        answers.append(True)
    except ValueError:
        answers.append(False)
print(json.dumps(answers))
`], { input: JSON.stringify(samples), encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1", PYTHONPATH: join(ROOT, "../../src") } });
  assert.equal(checked.status, 0, checked.stderr || String(checked.error));
  const service = JSON.parse(checked.stdout);
  samples.forEach(([text, maximum], index) => assert.equal(isMetricLabel(text, maximum), service[index], JSON.stringify(text)));
  assert.deepEqual(service.slice(0, 5), [true, true, true, true, true]);
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
