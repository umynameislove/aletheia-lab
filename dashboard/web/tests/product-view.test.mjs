/* UI contract test for the shared ProductView fixture.   Run: npm test
   Proves the UI reads p6-product-view/v1 exactly as the service returns it — no field renaming,
   no adapter — and checks each guarantee the service owner locked for K06, K07 and K08.
   Fixture: commit 4e37e5519a0ae22f4357671624c3ebc58c9aae2a, tests/fixtures/synthetic_p6_view.json. */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { COUNTERFACTUAL_MARKER } from "../src/service/markers.ts";
import { counterfactualStatus, evidenceRequests, observedInSnapshot, projectView } from "../src/data/projection.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const LOCKED_SHA = "0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2";
const raw = readFileSync(join(ROOT, "src/data/synthetic_p6_view.json"));
const V = JSON.parse(raw.toString("utf8"));

test("PV-A1 the UI reads the shared ProductView fixture byte for byte", () => {
  assert.equal(createHash("sha256").update(raw).digest("hex"), LOCKED_SHA);
});

test("PV-B1 the fixture is the UI's ProductView type as-is (strict tsc, unknown or renamed fields fail)", () => {
  const dir = mkdtempSync(join(tmpdir(), "p6-pv-"));
  try {
    writeFileSync(join(dir, "types.ts"), readFileSync(join(ROOT, "src/types.ts")));
    writeFileSync(join(dir, "check.ts"), `import type { ProductView } from "./types";\nexport const v: ProductView = ${raw.toString("utf8")};\n`);
    execFileSync(process.execPath, [join(ROOT, "node_modules/typescript/bin/tsc"), "--noEmit", "--strict", "--target", "ES2020",
      "--moduleResolution", "bundler", "--module", "ESNext", join(dir, "check.ts")], { stdio: "pipe" });
  } catch (e) {
    assert.fail("tsc rejected the fixture:\n" + (e.stdout?.toString() || e.message));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("PV-B2 the app imports the fixture directly, with no mapping layer", () => {
  const view = readFileSync(join(ROOT, "src/data/view.ts"), "utf8");
  assert.match(view, /import raw from "\.\/synthetic_p6_view\.json";/);
  assert.match(view, /export const VIEW = raw as ProductView;/);
});

test("PV-C1 schema and the required top-level fields", () => {
  assert.equal(V.schema_version, "p6-product-view/v1");
  for (const k of ["schema_version", "demo_only", "mode", "visibility", "project", "snapshot", "runtime", "result", "conversation", "claims", "evidence", "graph"])
    assert.ok(k in V, `missing ${k}`);
});

test("PV-C2 independent_families is null when unknown — never 0", () => {
  assert.equal(V.result.denominators.independent_families, null);
});

test("PV-C3 runtime is the deterministic mock with no external call", () => {
  assert.equal(V.runtime.provider, "deterministic_mock");
  assert.equal(V.runtime.external_call, false);
});

test("PV-C4 citations, counterevidence and visible evidence resolve inside the same view", () => {
  const ev = new Set(V.evidence.map(e => e.id));
  for (const c of V.claims) {
    c.citation_ids.forEach(id => assert.ok(ev.has(id), `${c.id} cites unknown ${id}`));
    c.counterevidence_ids.forEach(id => assert.ok(ev.has(id), `${c.id} counter-cites unknown ${id}`));
  }
  for (const t of V.conversation.turns) t.visible_evidence_ids.forEach(id => assert.ok(ev.has(id), `${t.id} sees unknown ${id}`));
});

test("PV-C5 nothing evaluator-only, withheld, secret or host-pathed reaches the JSON", () => {
  const text = raw.toString("utf8");
  assert.notEqual(V.visibility, "evaluator");
  for (const e of V.evidence) assert.notEqual(e.visibility, "evaluator", e.id);
  assert.ok(!/(^|["\s])(\/Users\/|\/home\/|[A-Za-z]:\\\\)/.test(text), "absolute host path in the view");
  assert.ok(!/api[_-]?key|secret|password|BEGIN [A-Z ]*PRIVATE KEY/i.test(text), "secret-looking content in the view");
});

test("PV-C6 every graph edge joins nodes that exist", () => {
  const nodes = new Set(V.graph.nodes.map(n => n.id));
  for (const e of V.graph.edges) assert.ok(nodes.has(e.source) && nodes.has(e.target), e.id);
});

/* ------------------------------------------------------------- K06 fields */
test("PV-D1 every turn carries its own result_id and runtime; the last turn belongs to this result", () => {
  for (const t of V.conversation.turns) {
    assert.equal(typeof t.result_id, "string", t.id);
    assert.deepEqual(Object.keys(t.runtime).sort(), ["external_call", "model", "provider"], t.id);
  }
  assert.equal(V.conversation.turns.at(-1).result_id, V.result.id);
});

test("PV-D2 claim_type is from the closed enum on every claim", () => {
  const ENUM = ["evidence_statement", "cause_assertion", "uncertainty_statement", "recommended_action", "other"];
  for (const c of V.claims) assert.ok(ENUM.includes(c.claim_type), `${c.id}: ${c.claim_type}`);
});

test("PV-D3 evidence carries reproduction_ref, never relative_path", () => {
  for (const e of V.evidence) {
    assert.ok(!("relative_path" in e), `${e.id} still has relative_path`);
    assert.equal(typeof e.reproduction_ref.record_id, "string");
    assert.ok(["snapshot", "snapshot_comparison", "regression_event"].includes(e.reproduction_ref.record_kind), e.id);
  }
});

test("PV-D4 structured metrics follow the locked rules", () => {
  const { metric_definitions: defs, metric_changes: changes } = V.snapshot;
  assert.ok(Array.isArray(defs) && Array.isArray(changes), "both arrays are always present");
  const names = defs.map(d => d.metric_name);
  assert.equal(new Set(names).size, names.length, "definitions are unique by metric_name");
  const metricEvidence = V.evidence.filter(e => e.kind === "metric_observation").map(e => e.id).sort();
  assert.deepEqual(changes.map(c => c.evidence_id).sort(), metricEvidence, "evidence_id is 1:1 with metric_observation evidence");
  assert.deepEqual(changes.map(c => c.evidence_id), [...changes.map(c => c.evidence_id)].sort(), "sorted by evidence_id");
  const seen = new Set();
  for (const c of changes) {
    assert.ok(names.includes(c.metric_name), `${c.evidence_id} has no definition`);
    if (c.before) assert.equal(c.before.snapshot_id, V.snapshot.baseline_id);
    if (c.after) assert.equal(c.after.snapshot_id, V.snapshot.id);
    if (c.kind === "added") { assert.equal(c.before, null); assert.equal(c.delta, null); assert.equal(c.adverse_status, "not_applicable"); }
    else if (c.kind === "removed") { assert.equal(c.after, null); assert.equal(c.delta, null); assert.equal(c.adverse_status, "not_applicable"); }
    else {
      assert.equal(c.before.run_id, c.after.run_id, "same run");
      assert.equal(c.before.step, c.after.step, "same step");
      assert.ok(Math.abs(c.after.value - c.before.value - c.delta) < 1e-9, "delta = after - before");
      assert.equal(c.kind, c.delta > 0 ? "increased" : "decreased");
    }
    const o = c.after ?? c.before;
    const id = JSON.stringify([o.run_id, c.metric_name, o.step]);
    assert.ok(!seen.has(id), `two changes for one (run, metric, step): ${id}`);
    seen.add(id);
  }
});

/* --------------------------------------------------------------- K07 marker */
test("PV-E1 the counterfactual marker appears exactly once, last, in the turn and the claim — and nowhere else", () => {
  const text = raw.toString("utf8");
  assert.equal(text.split(COUNTERFACTUAL_MARKER).length - 1, V.conversation.turns.length + V.claims.length);
  for (const x of [...V.conversation.turns, ...V.claims]) {
    assert.equal(x.missing_evidence.filter(m => m === COUNTERFACTUAL_MARKER).length, 1, x.id);
    assert.equal(x.missing_evidence.at(-1), COUNTERFACTUAL_MARKER, `${x.id}: marker is not last`);
  }
  assert.equal(counterfactualStatus(V), "not_available");
});

test("PV-E2 the marker is never counted as a request for evidence", () => {
  const t = V.conversation.turns[0];
  assert.deepEqual(evidenceRequests(t.missing_evidence), ["A checked mapping trace and a controlled recomputation"]);
  assert.deepEqual(evidenceRequests(V.claims[0].missing_evidence), []);
  const ROOTS = ["src/components", "src/service"];
  for (const dir of ROOTS) for (const f of readdirSync(join(ROOT, dir)).filter(f => /\.tsx?$/.test(f))) {
    const src = readFileSync(join(ROOT, dir, f), "utf8");
    assert.ok(!/missing_evidence\.length|missing_evidence\[0\]|missing_evidence\.join/.test(src), `${dir}/${f} reads raw missing_evidence`);
  }
});

/* ------------------------------------------------------------- K07 graph */
test("PV-F1 one Snapshot node (the analyzed one); OBSERVED_IN only joins EvidenceItem to it", () => {
  const snaps = V.graph.nodes.filter(n => n.kind === "Snapshot");
  assert.deepEqual(snaps.map(n => n.source_id), [V.snapshot.id]);
  const byId = new Map(V.graph.nodes.map(n => [n.id, n]));
  for (const e of V.graph.edges.filter(e => e.kind === "OBSERVED_IN")) {
    assert.equal(byId.get(e.source).kind, "EvidenceItem", e.id);
    assert.equal(byId.get(e.target).kind, "Snapshot", e.id);
  }
  for (const e of V.graph.edges) assert.ok(["OBSERVED_IN", "CITES", "ASSIGNED_DISPOSITION"].includes(e.kind), e.kind);
  assert.deepEqual([...observedInSnapshot(V)], ["demo-evidence-metric"]);
});

/* ------------------------------------------------ technical_failure invariant */
test("PV-G1 the failure projection keeps the envelope and evidence, and nothing that reads as a finding", () => {
  const f = projectView(V, { failed: true });
  assert.equal(f.result.status, "technical_failure");
  assert.equal(f.result.disposition, null);
  assert.deepEqual(f.claims, []);
  assert.equal(f.result.denominators.claims, 0);
  assert.deepEqual(f.evidence, V.evidence, "collected diagnosis-visible evidence stays");
  assert.ok(f.graph.nodes.every(n => n.kind === "Snapshot" || n.kind === "EvidenceItem"));
  assert.ok(f.graph.edges.every(e => e.kind === "OBSERVED_IN"));
  for (const t of f.conversation.turns) {
    assert.equal(t.abstained, true);
    assert.ok(!t.missing_evidence.includes(COUNTERFACTUAL_MARKER), "no marker under technical_failure");
    assert.equal(t.result_id, V.conversation.turns.find(x => x.id === t.id).result_id);
  }
  assert.equal(counterfactualStatus(f), "not_evaluated", "no marker is never read as available");
});
