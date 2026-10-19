/* DemoProductPort contract tests — JOB.md: "schema/required keys, bad fixture, backend ProductError,
   … không-network". Runs the real src/service modules under Node's type stripping. */

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DemoProductPort } from "../src/service/demo.ts";
import { ProductError, isProductError } from "../src/service/port.ts";
import { CODES } from "../src/service/demo.ts";
import { KNOWN_CODES, remedyFor } from "../src/service/errors.ts";
import { COUNTERFACTUAL_MARKER } from "../src/service/markers.ts";
import { canonicalQuestion, questionProblem } from "../src/service/question.ts";
import { projectView } from "../src/data/projection.ts";
import { declaredRuns, runMappings } from "../src/service/transitions.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = f => JSON.parse(readFileSync(join(ROOT, "src/data", f), "utf8"));
const VIEW = read("synthetic_p6_view.json");
const PREVIEW = read("p6_preview_import_v2.json");
const REQUIRED = ["schema_version", "demo_only", "mode", "visibility", "project", "snapshot", "runtime", "result", "conversation", "claims", "evidence", "graph"];

// no network, ever: any attempt fails the test that made it
const net = [];
globalThis.fetch = (...a) => { net.push(a); throw new Error("network call from the demo port"); };
globalThis.XMLHttpRequest = function () { net.push("xhr"); throw new Error("network call from the demo port"); };

let port;
beforeEach(() => { port = new DemoProductPort({ view: VIEW, preview: PREVIEW }); });

const rejects = async (p, code) => {
  try { await p; } catch (e) {
    assert.ok(isProductError(e), `expected ProductError, got ${e}`);
    assert.equal(e.code, code);
    assert.ok(!/\/Users\/|\/home\/|[A-Za-z]:\\|Traceback|at .*\(/.test(e.safe_message), "safe_message leaks a path or a stack");
    return e;
  }
  assert.fail(`expected ProductError ${code}`);
};

const MC = PREVIEW.mapping_candidates;
const T = MC.targets.find(t => t.available), MS = MC.metric_sources[0];
const okMapping = (over = {}) => ({
  target: { mapping_id: T.mapping_id, project_item_id: T.project_item_id, target_field: "entity_id", identifier_field: "run_id" },
  metric_sources: [{ mapping_id: MS.mapping_id, project_item_id: MS.project_item_id, format: MS.format, records_path: MS.records_path_candidates[0],
    metric_name_field: "name", metric_value_field: "value", run_id_field: "run_id", step_field: null }],
  runs: [{ run_id: "run-after", config_item_ids: [] }, { run_id: "run-before", config_item_ids: [] }],
  baseline_run_id: "run-before",
  metric_definitions: MS.metric_name_candidates.map(n => ({ metric_name: n, direction: "lower_is_better", regression_threshold: 0.05 })),
  ...over,
});
const src = (over) => ({ metric_sources: [{ ...okMapping().metric_sources[0], ...over }] });

/* ---------------------------------------------------------------- boundary */
test("P1 the port exposes exactly the nine contract methods, all async", () => {
  const names = ["demo_view", "preview_import", "confirm_import", "refresh", "analyze_mock", "follow_up", "view", "export_report", "delete_project"];
  for (const m of names) assert.equal(typeof port[m], "function", m);
  const own = Object.getOwnPropertyNames(DemoProductPort.prototype).filter(n => n !== "constructor" && n !== "reset");
  assert.deepEqual(own.sort(), [...names].sort());
  assert.equal(port.kind, "demo");
});

test("P2 bad fixture is refused with ProductError, not a crash later", () => {
  const broken = { ...VIEW }; delete broken.graph;
  assert.throws(() => new DemoProductPort({ view: broken, preview: PREVIEW }), e => e instanceof ProductError && e.code === "demo_integrity_error");
  assert.throws(() => new DemoProductPort({ view: { ...VIEW, schema_version: "v0" }, preview: PREVIEW }), e => e.code === "demo_integrity_error");
  assert.throws(() => new DemoProductPort({ view: VIEW, preview: {} }), e => e.code === "demo_integrity_error");
});

/* ------------------------------------------------------------- 1 demo_view */
test("M1 demo_view returns p6-product-view/v1 with every required key, demo_only and no external call", async () => {
  const v = await port.demo_view();
  for (const k of REQUIRED) assert.ok(k in v, k);
  assert.equal(v.schema_version, "p6-product-view/v1");
  assert.equal(v.demo_only, true);
  assert.equal(v.runtime.external_call, false);
  assert.deepEqual(v, VIEW, "the demo view is the shared fixture, unchanged");
});

test("M1b returned views are copies — a caller cannot mutate the port's state", async () => {
  const a = await port.demo_view(); a.claims.length = 0;
  assert.equal((await port.demo_view()).claims.length, VIEW.claims.length);
});

/* -------------------------------------------------------- 2 preview_import */
test("M2 preview_import returns the locked preview and refuses an empty root", async () => {
  assert.deepEqual(await port.preview_import("synthetic-ml-project"), PREVIEW);
  await rejects(port.preview_import("  "), "preview_failed");
});

/* -------------------------------------------------------- 3 confirm_import */
test("M3 confirm_import needs a live preview, confirms once, and returns status confirmed", async () => {
  await rejects(port.confirm_import(PREVIEW.preview_id, okMapping()), "preview_stale");
  await port.preview_import("x");
  const r = await port.confirm_import(PREVIEW.preview_id, okMapping());
  assert.deepEqual(Object.keys(r).sort(), ["project_id", "snapshot_id", "status"]);
  assert.equal(r.status, "confirmed");
  await rejects(port.confirm_import(PREVIEW.preview_id, okMapping()), "preview_already_confirmed");
});

test("M3b every mapping problem is mapping_invalid — the exact agreed payload, nothing else", async () => {
  const two = (a, b) => [{ metric_name: "accuracy", direction: "higher_is_better", regression_threshold: a }, { metric_name: "loss", direction: "lower_is_better", regression_threshold: b }];
  const cases = [
    { target: { ...okMapping().target, project_item_id: "nope" } },
    { target: { ...okMapping().target, mapping_id: "map-other" } },
    { target: { ...okMapping().target, target_field: "nope" } },
    { target: { ...okMapping().target, identifier_field: "nope" } },
    src({ mapping_id: "map-other" }),
    src({ format: "csv" }),
    src({ records_path: null }),                       // records_path is always an array
    src({ records_path: ["not", "offered"] }),
    src({ step_field: "nope" }),
    src({ metric_value_field: "nope" }),
    { runs: [{ run_id: "ghost", config_item_ids: [] }] },
    { runs: [{ run_id: "run-after", config_item_ids: ["not-offered"] }, { run_id: "run-before", config_item_ids: [] }] },
    { runs: [{ run_id: "run-after", config_item_ids: [] }] },  // baseline not among the runs
    { metric_definitions: [] },
    { metric_definitions: undefined },
    { metric_definitions: [{ metric_name: "accuracy", direction: "sideways", regression_threshold: 0.1 }, two(0, 0)[1]] },
    { metric_definitions: two(-1, 0.1) },
    { metric_definitions: two(Infinity, 0.1) },
    { metric_definitions: [two(0.1, 0.1)[0], { ...two(0.1, 0.1)[0], direction: "lower_is_better" }] },
    { metric_definitions: [two(0.1, 0.1)[0]] },        // loss missing
    // runs (Kiên, 2026-10-08): a run with observations left undeclared, and the same run as both
    { runs: [{ run_id: "run-after", config_item_ids: [] }], baseline_run_id: "run-after" },
  ];
  for (const over of cases) {
    await port.preview_import("x");
    await rejects(port.confirm_import(PREVIEW.preview_id, okMapping(over)), "mapping_invalid");
  }
  const csv = MC.metric_sources.find(m => m.format === "csv" && m.available);
  await port.preview_import("x");
  const ok = await port.confirm_import(PREVIEW.preview_id, okMapping({
    metric_sources: [{ mapping_id: csv.mapping_id, project_item_id: csv.project_item_id, format: "csv", records_path: [],
      metric_name_field: "metric", metric_value_field: "val", run_id_field: "run", step_field: null }],
    runs: [{ run_id: "run-after", config_item_ids: MC.runs[0].available_config_item_ids }, { run_id: "run-before", config_item_ids: [] }],
    metric_definitions: csv.metric_name_candidates.map(n => ({ metric_name: n, direction: "lower_is_better", regression_threshold: 1e3 })),
  }));
  assert.equal(ok.status, "confirmed", "CSV with records_path [], an offered config and 1e3 are all valid");
});

/* --------------------------------------------------------------- 4 refresh */
test("M4 refresh keeps the snapshot and says unchanged; unknown projects are not_found", async () => {
  const r = await port.refresh(VIEW.project.id);
  assert.deepEqual(r, { project_id: VIEW.project.id, snapshot_id: VIEW.snapshot.id, status: "unchanged" });
  await rejects(port.refresh("nope"), "record_not_found");
});

test("M3c a declared run with no metric observations in the source is refused", async () => {
  const preview = structuredClone(PREVIEW);
  preview.mapping_candidates.runs.find(r => r.run_id === "run-before").source_metric_item_ids = [];
  const p = new DemoProductPort({ view: VIEW, preview });
  await p.preview_import("x");
  await rejects(p.confirm_import(preview.preview_id, okMapping()), "mapping_invalid");
});

test("M3d three-plus selected runs retain per-run configs and confirm through the port", async () => {
  const preview = structuredClone(PREVIEW);
  const template = preview.mapping_candidates.runs.find(r => r.available && r.source_metric_item_ids.includes(MS.project_item_id));
  preview.mapping_candidates.runs.push({ ...structuredClone(template), run_id: "run-third" });
  preview.mapping_candidates.runs.push({ ...structuredClone(template), run_id: "run-fourth" });
  const configs = { "run-third": [...template.available_config_item_ids] };
  const selection = { runs: ["run-after", "run-before", "run-third", "run-fourth", "run-third"], baseline: "run-before", configs };
  const runs = runMappings(selection);
  assert.deepEqual(declaredRuns(selection), ["run-after", "run-before", "run-third", "run-fourth"]);
  assert.deepEqual(runs.find(r => r.run_id === "run-third").config_item_ids, configs["run-third"]);
  assert.deepEqual(runs.find(r => r.run_id === "run-fourth").config_item_ids, [], "no config is attached by default");
  assert.equal(runs.length, 4);
  const p = new DemoProductPort({ view: VIEW, preview });
  await p.preview_import("x");
  assert.equal((await p.confirm_import(preview.preview_id, okMapping({ runs }))).status, "confirmed");
});

test("M3e choosing a reference run includes it once without losing other selections", () => {
  assert.deepEqual(runMappings({ runs: ["a", "b"], baseline: "c", configs: { c: ["config-c"] } }), [
    { run_id: "a", config_item_ids: [] }, { run_id: "b", config_item_ids: [] }, { run_id: "c", config_item_ids: ["config-c"] },
  ]);
  assert.deepEqual(declaredRuns({ runs: ["a"], baseline: "a" }), ["a"]);
  assert.deepEqual(declaredRuns({ runs: [], baseline: "" }), []);
});

/* ---------------------------------------------------------- 5 analyze_mock */
test("M5 analyze_mock makes a new immutable mock view with per-turn result_id/runtime and no invented claim", async () => {
  const v = await port.analyze_mock(VIEW.project.id, VIEW.snapshot.id, "Why did loss rise?");
  for (const k of REQUIRED) assert.ok(k in v, k);
  assert.notEqual(v.result.id, VIEW.result.id);
  assert.equal(v.result.causal_status, "unverified");
  assert.equal(v.result.denominators.independent_families, null);
  assert.deepEqual(v.claims, []);
  const t = v.conversation.turns[0];
  assert.equal(t.result_id, v.result.id);
  assert.deepEqual(t.runtime, VIEW.runtime);
  assert.deepEqual(await port.view(v.result.id), v, "the result reloads exactly");
  // K07: no valid counterfactual pair in the demo → the marker, once, last
  assert.deepEqual(t.missing_evidence, [COUNTERFACTUAL_MARKER]);
  // OBSERVED_IN only for evidence the analyzed snapshot actually observes
  const byId = new Map(v.graph.nodes.map(n => [n.id, n]));
  assert.ok(v.graph.edges.every(e => e.kind === "OBSERVED_IN" && byId.get(e.source).kind === "EvidenceItem" && byId.get(e.target).kind === "Snapshot"));
  assert.equal(v.graph.edges.length, 1);
  await rejects(port.analyze_mock(VIEW.project.id, "foreign-snapshot", "q"), "record_not_found");
  await rejects(port.analyze_mock(VIEW.project.id, VIEW.snapshot.id, " "), "invalid_question");
});

/* ------------------------------------------------------------- 6 follow_up */
const CLAIM = VIEW.claims[0];
const nodeOf = (v, kind) => v.graph.nodes.find(n => n.kind === kind);

test("M6 follow_up on a claim: parent claims as prefix plus one uncertainty_statement, a result of its own", async () => {
  const v = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "Is the drop within noise?");
  assert.notEqual(v.result.id, VIEW.result.id);
  assert.equal(v.project.id, VIEW.project.id);
  assert.deepEqual(v.snapshot, VIEW.snapshot, "same snapshot");
  assert.deepEqual(v.runtime, VIEW.runtime, "same runtime");
  assert.deepEqual(v.evidence, VIEW.evidence, "same authorized evidence");
  // conversation: every old turn untouched, exactly one new turn
  assert.deepEqual(v.conversation.turns.slice(0, -1), VIEW.conversation.turns);
  const t = v.conversation.turns.at(-1);
  assert.equal(v.conversation.turns.length, VIEW.conversation.turns.length + 1);
  assert.equal(t.result_id, v.result.id);
  assert.equal(t.abstained, true);
  assert.equal(t.question, "Is the drop within noise?");
  assert.deepEqual(t.visible_evidence_ids, [...new Set([...CLAIM.citation_ids, ...CLAIM.counterevidence_ids])].sort());
  // inherited verbatim from the parent's last turn (Kiên, 2026-10-09): marker once, last
  assert.deepEqual(t.missing_evidence, VIEW.conversation.turns.at(-1).missing_evidence);
  // claims: the parent's as a prefix, then exactly one new uncertainty_statement
  assert.deepEqual(v.claims.slice(0, -1), VIEW.claims);
  const c = v.claims.at(-1);
  assert.equal(v.claims.length, VIEW.claims.length + 1);
  assert.equal(c.turn_id, t.id);
  assert.equal(c.claim_type, "uncertainty_statement");
  assert.equal(c.epistemic_state, "abstained");
  assert.equal(c.support, "not_estimated");
  assert.equal(c.max_strength, "abstain");
  assert.deepEqual(c.missing_evidence, VIEW.conversation.turns.at(-1).missing_evidence);
  assert.deepEqual(c.citation_ids, CLAIM.citation_ids, "citation keeps its role and order");
  assert.deepEqual(c.counterevidence_ids, CLAIM.counterevidence_ids, "counterevidence keeps its role and order");
  // the result is the follow-up's own, not the parent's verdict
  assert.equal(v.result.status, "complete");
  assert.equal(v.result.disposition, "abstain");
  assert.equal(v.result.causal_status, "unverified");
  assert.notEqual(v.result.summary, VIEW.result.summary);
  assert.deepEqual(v.result.denominators, { independent_families: null, contexts: VIEW.result.denominators.contexts,
    outputs: VIEW.result.denominators.outputs, claims: v.claims.length });
  // parent is immutable
  assert.deepEqual(await port.view(VIEW.result.id), VIEW);
});

test("M6i a follow-up never adds the marker: a parent without it gives a child without it", async () => {
  const noMarker = structuredClone(VIEW);
  for (const t of noMarker.conversation.turns) t.missing_evidence = t.missing_evidence.filter(m => m !== COUNTERFACTUAL_MARKER);
  const p = new DemoProductPort({ view: noMarker, preview: PREVIEW });
  const v = await p.follow_up(noMarker.result.id, "claim", CLAIM.id, "q");
  assert.deepEqual(v.conversation.turns.at(-1).missing_evidence, ["A checked mapping trace and a controlled recomputation"]);
  assert.deepEqual(v.claims.at(-1).missing_evidence, ["A checked mapping trace and a controlled recomputation"]);
});

test("M6b the Disposition node is scoped to the child; the parent's is not reused", async () => {
  const v = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "q");
  const parentDisp = nodeOf(VIEW, "Disposition"), childDisp = nodeOf(v, "Disposition");
  assert.notEqual(childDisp.id, parentDisp.id);
  assert.equal(childDisp.source_id, v.result.id);
  assert.ok(!v.graph.nodes.some(n => n.id === parentDisp.id));
  const ids = new Set(v.graph.nodes.map(n => n.id));
  for (const e of v.graph.edges) assert.ok(ids.has(e.source) && ids.has(e.target), e.id);
  // the parent's node is stale in the child: following up on it is refused
  await rejects(port.follow_up(v.result.id, "node", parentDisp.id, "q"), "selection_not_available");
});

test("M6c node selections resolve to their own scope", async () => {
  const viaNode = await port.follow_up(VIEW.result.id, "node", nodeOf(VIEW, "AtomicClaim").id, "same as the claim?");
  const viaClaim = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "same as the claim?");
  assert.deepEqual(viaNode.conversation.turns.at(-1).visible_evidence_ids, viaClaim.conversation.turns.at(-1).visible_evidence_ids);
  assert.deepEqual(viaNode.claims.at(-1).citation_ids, CLAIM.citation_ids);

  const ev = nodeOf(VIEW, "EvidenceItem");
  const e = await port.follow_up(VIEW.result.id, "node", ev.id, "this evidence?");
  assert.deepEqual(e.conversation.turns.at(-1).visible_evidence_ids, [ev.source_id]);
  assert.deepEqual(e.claims.at(-1).citation_ids, [ev.source_id]);
  assert.deepEqual(e.claims.at(-1).counterevidence_ids, []);

  for (const kind of ["Snapshot", "Disposition"]) {
    const x = await port.follow_up(VIEW.result.id, "node", nodeOf(VIEW, kind).id, `and the ${kind}?`);
    assert.deepEqual(x.conversation.turns.at(-1).visible_evidence_ids, [], `${kind} scopes to nothing`);
  }
});

test("M6d replaying the exact tuple returns the same child; any difference makes a new one", async () => {
  const a = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "Is the drop within noise?");
  const b = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "Is the drop within noise?");
  assert.deepEqual(b, a);
  assert.equal(b.conversation.turns.at(-1).id, a.conversation.turns.at(-1).id);
  const c = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "Is the drop within noise ?");
  assert.notEqual(c.result.id, a.result.id, "the question is not normalised for identity");
});

test("M6e a parent chain: each child keeps every earlier turn and its result_id", async () => {
  const a = await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "first");
  const b = await port.follow_up(a.result.id, "claim", a.claims.at(-1).id, "second");
  assert.equal(b.conversation.turns.length, VIEW.conversation.turns.length + 2);
  assert.deepEqual(b.conversation.turns.map(t => t.result_id), [...VIEW.conversation.turns.map(t => t.result_id), a.result.id, b.result.id]);
  assert.deepEqual(b.claims.slice(0, a.claims.length), a.claims);
  assert.deepEqual(await port.view(a.result.id), a, "the middle result reloads unchanged");
});

test("M6f a technical_failure parent stays failed: no claim, no disposition, no marker", async () => {
  const failed = projectView(VIEW, { failed: true });
  const p = new DemoProductPort({ view: failed, preview: PREVIEW });
  const v = await p.follow_up(failed.result.id, "node", nodeOf(failed, "Snapshot").id, "what happened?");
  assert.equal(v.result.status, "technical_failure");
  assert.equal(v.result.disposition, null);
  assert.deepEqual(v.claims, []);
  assert.equal(v.result.denominators.claims, 0);
  const t = v.conversation.turns.at(-1);
  assert.equal(t.abstained, true);
  assert.ok(!t.missing_evidence.includes(COUNTERFACTUAL_MARKER));
  assert.ok(v.graph.nodes.every(n => n.kind === "Snapshot" || n.kind === "EvidenceItem"));
  assert.ok(v.graph.edges.every(e => e.kind === "OBSERVED_IN"));
  const ev = await p.follow_up(failed.result.id, "node", nodeOf(failed, "EvidenceItem").id, "and this?");
  assert.equal(ev.result.status, "technical_failure");
  assert.deepEqual(ev.claims, []);
  await rejects(p.follow_up(failed.result.id, "claim", CLAIM.id, "q"), "selection_not_available");
});

test("M6g follow_up refusals use the service's codes", async () => {
  await rejects(port.follow_up(VIEW.result.id, "evidence", CLAIM.id, "q"), "invalid_selection_kind");
  await rejects(port.follow_up(VIEW.result.id, "Claim", CLAIM.id, "q"), "invalid_selection_kind");
  await rejects(port.follow_up(VIEW.result.id, "claim", "claim-from-elsewhere", "q"), "selection_not_available");
  await rejects(port.follow_up(VIEW.result.id, "node", "node-dangling", "q"), "selection_not_available");
  await rejects(port.follow_up("not a valid id!", "claim", CLAIM.id, "q"), "invalid_id");
  await rejects(port.follow_up("", "claim", CLAIM.id, "q"), "invalid_id");
  await rejects(port.follow_up("nope", "claim", CLAIM.id, "q"), "record_not_found");
  for (const q of ["", " leading", "trailing ", "two\nlines", "tab\there", "x".repeat(2001), "Tại sao".normalize("NFD")])
    await rejects(port.follow_up(VIEW.result.id, "claim", CLAIM.id, q), "invalid_question");
  // the limit is characters, not UTF-16 units: 2000 astral characters are accepted
  await port.follow_up(VIEW.result.id, "claim", CLAIM.id, "𝟙".repeat(2000));
});

test("M6h the UI canonicalises what was typed so a correct question is never refused for its encoding", () => {
  const typed = "  Tại sao độ chính xác giảm?  ".normalize("NFD");
  const sent = canonicalQuestion(typed);
  assert.equal(sent, "Tại sao độ chính xác giảm?".normalize("NFC"));
  assert.equal(questionProblem(sent), null);
  assert.ok(questionProblem(typed), "the raw NFD, padded string would be refused");
  assert.ok(questionProblem(canonicalQuestion("a\nb")), "a line break is still refused, not silently removed");
});

/* ------------------------------------------------------------------ 7 view */
test("M7 view reloads exactly and refuses unknown ids", async () => {
  assert.deepEqual(await port.view(VIEW.result.id), VIEW);
  await rejects(port.view("nope"), "record_not_found");
});

/* --------------------------------------------------------- 8 export_report */
test("M8 export_report gives bytes for json and markdown; pdf is refused in the demo", async () => {
  const j = await port.export_report(VIEW.result.id, "json");
  assert.ok(j instanceof Uint8Array);
  assert.deepEqual(JSON.parse(new TextDecoder().decode(j)), VIEW, "json export is the same projection the UI reads");
  const md = new TextDecoder().decode(await port.export_report(VIEW.result.id, "markdown"));
  assert.match(md, /not estimated \(null — not zero\)/);
  assert.match(md, new RegExp(VIEW.claims[0].citation_ids[0]));
  assert.ok(!/\/Users\/|\/home\/|api[_-]?key/i.test(md));
  await rejects(port.export_report(VIEW.result.id, "pdf"), "feature_unavailable");   // the service renders PDF; the demo port does not
  // K10: exact formats only — no alias, no case folding, no padding
  for (const f of ["docx", "JSON", "md", "pdf ", "Markdown", ""]) await rejects(port.export_report(VIEW.result.id, f), "invalid_request");
  await rejects(port.export_report("not a valid id!", "json"), "invalid_id");
  await rejects(port.export_report("nope", "json"), "record_not_found");
  // same result + format → the same bytes, and a follow-up never changes the parent's export
  const before = await port.export_report(VIEW.result.id, "markdown");
  await port.follow_up(VIEW.result.id, "claim", VIEW.claims[0].id, "does the export change?");
  assert.deepEqual(await port.export_report(VIEW.result.id, "markdown"), before);
});

/* -------------------------------------------------------- 9 delete_project */
test("M9 delete_project returns a content-free receipt, is idempotent, and closes the project's results", async () => {
  const r = await port.delete_project(VIEW.project.id);
  assert.deepEqual(Object.keys(r).sort(), ["deleted_count", "project_id", "retained_shared_count", "status"]);
  // 2 snapshots (current + baseline) + 1 result + every turn, claim, evidence item, node and edge — each once
  assert.equal(r.deleted_count, 2 + 1 + VIEW.conversation.turns.length + VIEW.claims.length + VIEW.evidence.length + VIEW.graph.nodes.length + VIEW.graph.edges.length);
  assert.deepEqual(await port.delete_project(VIEW.project.id), r, "a second call returns the same receipt");
  await rejects(port.view(VIEW.result.id), "record_not_found");
  await rejects(port.refresh(VIEW.project.id), "record_not_found");
  await rejects(port.delete_project("nope"), "record_not_found");
});

/* ------------------------------------------------------------------ whole */
test("P3 deterministic: the same calls in the same order give the same answers", async () => {
  const run = async () => {
    const p = new DemoProductPort({ view: VIEW, preview: PREVIEW });
    await p.preview_import("x");
    const c = await p.confirm_import(PREVIEW.preview_id, okMapping());
    const a = await p.analyze_mock(VIEW.project.id, VIEW.snapshot.id, "q");
    const f = await p.follow_up(a.result.id, "node", a.graph.nodes[0].id, "q2");
    return { c, a, f };
  };
  assert.deepEqual(await run(), await run());
});

test("P4 reset puts the session back to the packaged state", async () => {
  await port.delete_project(VIEW.project.id);
  port.reset();
  assert.deepEqual(await port.view(VIEW.result.id), VIEW);
});

test("P6 every code the demo port can raise is on the service's own list", () => {
  for (const c of Object.values(CODES)) assert.ok(KNOWN_CODES.includes(c), `${c} is not a service code`);
});

test("E1 remediation: known codes advise, import_<issue> passes through, unknown codes fall back safely", () => {
  assert.ok(remedyFor("mapping_invalid").advice);
  assert.match(remedyFor("import_secret_withheld").advice, /secret_withheld/);
  const u = remedyFor("brand_new_code");
  assert.equal(u.known, false);
  assert.equal(u.advice, null, "an unknown code shows the safe message alone — nothing invented");
});

test("P5 no network: no method touched fetch or XMLHttpRequest", () => {
  assert.deepEqual(net, []);
  const src = ["demo.ts", "port.ts", "report.ts"].map(f => readFileSync(join(ROOT, "src/service", f), "utf8")).join("\n");
  assert.ok(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|navigator\.sendBeacon/.test(src), "network API referenced in the demo port");
});
