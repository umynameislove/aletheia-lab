/* Lineage layout (Q08): deterministic under input permutation, fail-closed on dangling or
   duplicate IDs, no invented edges, and graph ↔ table parity on the shared fixture.
   Timing is measured separately (bench.html), never here.   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { LANE_KINDS, lineageLayout } from "../src/data/lineage.ts";
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
