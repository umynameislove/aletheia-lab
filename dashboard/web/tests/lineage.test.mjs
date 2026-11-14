/* Lineage layout (Q08): deterministic under input permutation, fail-closed on dangling or
   duplicate IDs, no invented edges, and graph ↔ table parity on the shared fixture.
   Timing is measured separately (bench.html), never here.   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { LANE_KINDS, lineageLayout, ZOOM_MAX, ZOOM_MIN, ZOOM_STEPS, zoomKey, zoomStep } from "../src/data/lineage.ts";
import { shuffled, syntheticGraph } from "../src/data/syntheticGraph.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const VIEW = JSON.parse(readFileSync(join(ROOT, "src/data/synthetic_p6_view.json"), "utf8"));
const shape = l => ({ lanes: l.lanes.map(lane => lane.map(n => n.id)), links: l.links, edges: l.edges.map(e => e.id) });

test("G1 the same graph lays out identically whatever order it arrives in", () => {
  const g = syntheticGraph(1000);
  const reference = shape(lineageLayout(g.nodes, g.edges));
  for (let seed = 1; seed <= 25; seed++) {
    assert.deepEqual(shape(lineageLayout(shuffled(g.nodes, seed), shuffled(g.edges, seed * 31))), reference, `seed ${seed}`);
  }
});

test("G2 a 1,000-node graph places every node in its lane", () => {
  const g = syntheticGraph(1000);
  const l = lineageLayout(g.nodes, g.edges);
  assert.equal(g.nodes.length, 1000);
  assert.equal(l.lanes.reduce((n, lane) => n + lane.length, 0), 1000);
  assert.equal(l.unplaced, 0);
  assert.deepEqual(l.lanes.map(lane => lane.length), [1, g.evidenceIds.length, g.claimIds.length, 1]);
  assert.deepEqual(l.links, [["OBSERVED_IN"], ["CITES"], ["ASSIGNED_DISPOSITION"]]);
});

test("G3 a dangling edge or a reused node ID is reported, never repaired", () => {
  const g = syntheticGraph(20);
  const dangling = [...g.edges, { id: "edge-x", kind: "CITES", source: g.nodes[3].id, target: "node-that-is-not-there", visibility: "diagnosis" }];
  const a = lineageLayout(g.nodes, dangling);
  assert.deepEqual(a.dangling, ["edge-x"]);
  assert.equal(a.edges.length, dangling.length, "the bad edge is reported, not dropped silently");
  const b = lineageLayout([...g.nodes, { ...g.nodes[5] }], g.edges);
  assert.deepEqual(b.duplicates, [g.nodes[5].id]);
  const ok = lineageLayout(g.nodes, g.edges);
  assert.deepEqual([ok.dangling, ok.duplicates], [[], []]);
});

test("G4 no edge is added: the text path is exactly the projection's edges", () => {
  const l = lineageLayout(VIEW.graph.nodes, VIEW.graph.edges);
  assert.deepEqual(new Set(l.edges.map(e => e.id)), new Set(VIEW.graph.edges.map(e => e.id)));
  assert.ok(l.edges.every(e => ["OBSERVED_IN", "CITES", "ASSIGNED_DISPOSITION"].includes(e.kind)));
});

test("G5 graph and claim table agree on the shared fixture", () => {
  const l = lineageLayout(VIEW.graph.nodes, VIEW.graph.edges);
  const [, evidence, claims] = l.lanes;
  assert.deepEqual(new Set(claims.map(n => n.source_id)), new Set(VIEW.claims.map(c => c.id)), "every claim row has one claim node and back");
  const known = new Set(VIEW.evidence.map(e => e.id));
  assert.ok(evidence.every(n => known.has(n.source_id)), "every evidence node is a listed evidence item");
  assert.equal(l.unplaced, 0);
  assert.deepEqual(LANE_KINDS, ["Snapshot", "EvidenceItem", "AtomicClaim", "Disposition"]);
});

test("G6 zoom moves one step at a time, stops at both ends, and 0 resets to 100%", () => {
  assert.deepEqual([...ZOOM_STEPS], [0.8, 1, 1.25, 1.5, 2]);
  // 0.8 of a ~30 px node keeps it at the 24 px minimum target size (WCAG 2.5.8)
  assert.ok(ZOOM_MIN * 30 >= 24);
  const walk = (from, by, n) => { let z = from; const seen = []; for (let i = 0; i < n; i++) seen.push(z = zoomStep(z, by)); return seen; };
  assert.deepEqual(walk(1, 1, 6), [1.25, 1.5, 2, 2, 2, 2]);
  assert.deepEqual(walk(1, -1, 3), [0.8, 0.8, 0.8]);
  assert.equal(zoomStep(ZOOM_MAX, 1), ZOOM_MAX);
  assert.equal(zoomStep(ZOOM_MIN, -1), ZOOM_MIN);
  for (const z of ZOOM_STEPS) assert.equal(zoomStep(z, 0), 1);
  // a level between steps goes to the nearest step on that side; nonsense resets
  assert.equal(zoomStep(1.1, 1), 1.25);
  assert.equal(zoomStep(1.1, -1), 1);
  assert.equal(zoomStep(5, 1), ZOOM_MAX);
  assert.equal(zoomStep(0.1, -1), ZOOM_MIN);
  assert.equal(zoomStep(Number.NaN, 1), 1);
});

test("G7 + - 0 zoom the drawing; with Ctrl, Cmd or Alt they stay the browser's page zoom", () => {
  const k = (key, mods = {}) => zoomKey({ key, ctrlKey: false, metaKey: false, altKey: false, ...mods });
  assert.deepEqual(["+", "=", "-", "0"].map(x => k(x)), [1, 1, -1, 0]);
  for (const mod of ["ctrlKey", "metaKey", "altKey"]) {
    for (const key of ["+", "=", "-", "0"]) assert.equal(k(key, { [mod]: true }), null, `${mod} ${key}`);
  }
  // the arrow keys belong to the investigation's claim list, and other keys are not taken
  for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "j", "k", "x", "e", "/", "?", "1", "_"]) assert.equal(k(key), null, key);
});
