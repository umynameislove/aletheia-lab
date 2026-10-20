/* DemoProductPort — PRODUCT_CONTRACT §1: "Quân tạo … DemoProductPort chạy fixture tổng hợp
   khi branch Kiên chưa có."

   Offline and deterministic: no network, no filesystem, no clock, no randomness. State lives in
   memory for the session. IDs come from a counter, so the same calls in the same order always
   give the same answers. Fixtures are injected (not imported) so the contract tests run this
   exact module under Node.

   Confirmed by the service owner (2026-10-05):
     status values   confirm_import → "confirmed"; delete_project → "deleted", a repeat call returns
                     the same immutable receipt
     error codes     taken from the service's list. Every mapping problem is mapping_invalid — the
                     service does not surface P3 codes like unknown_item from confirm_import.
   Locked by the service owner for K07/K08 (2026-10-08), and followed here:
     analyze_mock    no valid counterfactual pair → the counterfactual marker, once, last in
                     missing_evidence; OBSERVED_IN only for evidence observed in the analyzed snapshot
     follow_up       invalid_selection_kind / selection_not_available / invalid_id (malformed result
                     ID) / record_not_found / invalid_question; exact-tuple replay returns the same
                     child; the child keeps the parent's claims as a prefix and adds one
                     uncertainty_statement, or stays technical_failure with no claim; the new
                     turn and claim inherit the parent's last turn missing_evidence
   The UI falls back gracefully for any code it does not know. */

import type { Claim, ConfirmMapping, GraphNode, MetricSourceCandidate, PreviewImportResult, ProductView, Turn } from "../types.ts";
import { ProductError } from "./port.ts";
import type { ConfirmResult, DeleteReceipt, ProductPort, RefreshResult, ReportFormat, SelectionKind } from "./port.ts";
import { reportMarkdown } from "./report.ts";
import { COUNTERFACTUAL_MARKER } from "./markers.ts";
import { questionProblem } from "./question.ts";

export const STATUS = { confirmed: "confirmed", deleted: "deleted" } as const;
export const CODES = {
  integrity: "demo_integrity_error", notFound: "record_not_found", invalidId: "invalid_id",
  previewFailed: "preview_failed", previewBlocked: "preview_blocked", previewStale: "preview_stale",
  previewConfirmed: "preview_already_confirmed", mapping: "mapping_invalid",
  question: "invalid_question", unavailable: "feature_unavailable",
  selectionKind: "invalid_selection_kind", selectionMissing: "selection_not_available",
  request: "invalid_request",
} as const;

/** Well-formed result ID. Malformed → invalid_id; well-formed but unknown → record_not_found. */
const RESULT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

/* The follow-up result is its own record, not a copy of the parent's verdict. */
const FOLLOW_UP = {
  summary: "Deterministic mock follow-up. No new evidence was read, so the question stays open.",
  allowed_wording: "The follow-up question was recorded against the same snapshot and evidence scope.",
  forbidden_wording: "The follow-up answered the question or established a cause.",
  caveat: "Deterministic mock, offline; no model was run and no evidence was added.",
} as const;

const REQUIRED_VIEW_KEYS = ["schema_version", "demo_only", "mode", "visibility", "project", "snapshot",
  "runtime", "result", "conversation", "claims", "evidence", "graph"] as const;
const DIRECTIONS = new Set(["higher_is_better", "lower_is_better"]);

export interface DemoFixtures {
  view: ProductView;
  preview: PreviewImportResult;
}

interface ProjectState {
  snapshots: string[];
  deleted: boolean;
  receipt: DeleteReceipt | null;
}

const clone = <T>(x: T): T => structuredClone(x);
const pad = (n: number) => String(n).padStart(3, "0");
const text = (x: unknown) => (typeof x === "string" ? x.trim() : "");
/** Evidence IDs the view's graph observes in its snapshot (EvidenceItem —OBSERVED_IN→ Snapshot). */
const observedIds = (v: ProductView) => {
  const byId = new Map(v.graph.nodes.map(x => [x.id, x]));
  return new Set(v.graph.edges.filter(e => e.kind === "OBSERVED_IN" && byId.get(e.target)?.kind === "Snapshot")
    .map(e => byId.get(e.source)).filter(x => x?.kind === "EvidenceItem").map(x => x!.source_id));
};

export class DemoProductPort implements ProductPort {
  readonly kind = "demo" as const;
  #fx: DemoFixtures;
  #results = new Map<string, ProductView>();
  #projects = new Map<string, ProjectState>();
  #previews = new Set<string>();
  #confirmed = new Set<string>();
  /** exact (result_id, selection_kind, selection_id, question) → child result id */
  #children = new Map<string, string>();
  #seq = 1;

  constructor(fx: DemoFixtures) {
    const v = fx?.view as unknown as Record<string, unknown> | undefined;
    const missing = REQUIRED_VIEW_KEYS.filter(k => !v || !(k in v));
    if (missing.length) throw new ProductError(CODES.integrity, `The demo view is missing: ${missing.join(", ")}.`);
    if (fx.view.schema_version !== "p6-product-view/v1") throw new ProductError(CODES.integrity, "The demo view is not p6-product-view/v1.");
    if (!fx.preview || typeof fx.preview.preview_id !== "string" || !fx.preview.mapping_candidates)
      throw new ProductError(CODES.integrity, "The demo preview is not a preview_import result.");
    this.#fx = clone(fx);
    this.reset();
  }

  /** Demo control only — not one of the nine methods. Puts the session back to the packaged state. */
  reset(): void {
    const v = this.#fx.view;
    this.#results = new Map([[v.result.id, clone(v)]]);
    this.#projects = new Map([[v.project.id, { snapshots: [v.snapshot.baseline_id, v.snapshot.id], deleted: false, receipt: null }]]);
    this.#previews = new Set();
    this.#confirmed = new Set();
    this.#children = new Map();
    this.#seq = 1;
  }

  /* ------------------------------------------------------------------- 1 */
  async demo_view(): Promise<ProductView> {
    return clone(this.#fx.view);
  }

  /* ------------------------------------------------------------------- 2 */
  async preview_import(root: string): Promise<PreviewImportResult> {
    // the demo never opens a folder; it only refuses to preview nothing
    if (!text(root)) throw new ProductError(CODES.previewFailed, "No folder was granted.");
    this.#previews.add(this.#fx.preview.preview_id);
    return clone(this.#fx.preview);
  }

  /* ------------------------------------------------------------------- 3 */
  async confirm_import(preview_id: string, mapping: ConfirmMapping): Promise<ConfirmResult> {
    if (this.#confirmed.has(preview_id))
      throw new ProductError(CODES.previewConfirmed, "This preview was already confirmed.");
    if (!this.#previews.has(preview_id))
      throw new ProductError(CODES.previewStale, "This preview is no longer valid.");
    const p = this.#fx.preview;
    if (p.blockers.length) throw new ProductError(CODES.previewBlocked, "The scan reported a blocker.");
    // typed for callers, but checked like untrusted input — as the service does
    this.#validateMapping(mapping as unknown as Record<string, unknown>);
    this.#previews.delete(preview_id); // a preview confirms once
    this.#confirmed.add(preview_id);
    const n = this.#seq++;
    const project_id = `demo-project-${pad(100 + n)}`, snapshot_id = `demo-snapshot-${pad(100 + n)}`;
    this.#projects.set(project_id, { snapshots: [snapshot_id], deleted: false, receipt: null });
    return { project_id, snapshot_id, status: STATUS.confirmed };
  }

  /* The exact confirm_import mapping agreed with the service owner. IDs and format are taken
     verbatim from mapping_candidates; records_path is always an array (CSV: []). */
  #validateMapping(m: Record<string, unknown>): void {
    const C = this.#fx.preview.mapping_candidates;
    const bad = (msg: string): never => { throw new ProductError(CODES.mapping, msg); };
    const strs = (x: unknown) => Array.isArray(x) && x.every(v => typeof v === "string");

    const target = (m.target ?? {}) as Record<string, unknown>;
    const t = C.targets.find(c => c.project_item_id === target.project_item_id && c.available);
    if (!t) bad("The target is not a collected, available item.");
    if (target.mapping_id !== t!.mapping_id) bad("target.mapping_id must be the candidate's mapping_id.");
    if (!t!.field_names.includes(text(target.target_field))) bad("target.target_field does not exist on the target.");
    if (!t!.field_names.includes(text(target.identifier_field))) bad("target.identifier_field does not exist on the target.");

    const sources = Array.isArray(m.metric_sources) ? (m.metric_sources as Record<string, unknown>[]) : [];
    if (!sources.length) bad("At least one metric source is required.");
    const used: MetricSourceCandidate[] = [];
    for (const s of sources) {
      const ms = C.metric_sources.find(c => c.project_item_id === s.project_item_id && c.available);
      if (!ms) bad("A metric source is not a collected, available item.");
      if (s.mapping_id !== ms!.mapping_id) bad("metric_sources[].mapping_id must be the candidate's mapping_id.");
      if (s.format !== ms!.format) bad("metric_sources[].format must be the candidate's format.");
      if (!strs(s.records_path)) bad("records_path must be an array of path segments.");
      const rp = s.records_path as string[];
      if (ms!.format === "csv" && rp.length) bad("records_path must be [] for a CSV source.");
      if (ms!.records_path_candidates.length && !ms!.records_path_candidates.some(c => JSON.stringify(c) === JSON.stringify(rp)))
        bad("records_path must be one of the offered candidates.");
      for (const f of ["metric_name_field", "metric_value_field", "run_id_field"])
        if (!ms!.field_names.includes(text(s[f]))) bad(`${f} does not exist on the metric source.`);
      if (s.step_field !== null && !ms!.field_names.includes(text(s.step_field))) bad("step_field must be null or a field on the source.");
      used.push(ms!);
    }

    const runs = Array.isArray(m.runs) ? (m.runs as Record<string, unknown>[]) : [];
    if (!runs.length) bad("At least one run is required.");
    const ids = runs.map(r => text(r.run_id));
    if (new Set(ids).size !== ids.length) bad("run_id values must be unique.");
    for (const r of runs) {
      const rc = C.runs.find(c => c.available && c.run_id === r.run_id);
      if (!rc) bad("Every run must be a known, available run.");
      if (!strs(r.config_item_ids) || !(r.config_item_ids as string[]).every(id => rc!.available_config_item_ids.includes(id)))
        bad("config_item_ids must come from the run's available configs.");
    }
    if (!ids.includes(text(m.baseline_run_id))) bad("baseline_run_id must name one of the runs.");
    // every run with observations in a chosen source is declared; a declared run without any is refused
    const observedRuns = new Set(C.runs.filter(r => r.available && used.some(u => r.source_metric_item_ids.includes(u.project_item_id))).map(r => r.run_id));
    if ([...observedRuns].some(r => !ids.includes(r))) bad("Every run with metric observations must be declared in runs.");
    // only when the preview names the runs it saw in the chosen source(s)
    if (observedRuns.size && ids.some(r => !observedRuns.has(r))) bad("A declared run has no metric observations.");

    // K04d — no defaults
    const defs = Array.isArray(m.metric_definitions) ? (m.metric_definitions as Record<string, unknown>[]) : null;
    if (!defs || !defs.length) bad("metric_definitions is missing or empty.");
    const names = defs!.map(d => text(d.metric_name));
    if (names.some(x => !x)) bad("Every metric definition needs a metric_name.");
    if (new Set(names).size !== names.length) bad("metric_name values must be unique.");
    for (const d of defs!) {
      if (!DIRECTIONS.has(d.direction as string)) bad("direction must be higher_is_better or lower_is_better.");
      const t2 = d.regression_threshold;
      if (typeof t2 !== "number" || !Number.isFinite(t2) || t2 < 0) bad("regression_threshold must be a finite number >= 0.");
    }
    const observed = new Set(used.flatMap(s => s.metric_name_candidates));
    if (observed.size && (observed.size !== names.length || !names.every(x => observed.has(x))))
      bad("metric_definitions must match the observed metric names exactly.");
  }

  /* ------------------------------------------------------------------- 4 */
  async refresh(project_id: string): Promise<RefreshResult> {
    const p = this.#live(project_id);
    // the demo source never changes, so a refresh always keeps the current snapshot
    return { project_id, snapshot_id: p.snapshots[p.snapshots.length - 1], status: "unchanged" };
  }

  /* ------------------------------------------------------------------- 5 */
  async analyze_mock(project_id: string, snapshot_id: string, question: string): Promise<ProductView> {
    const p = this.#live(project_id);
    if (!p.snapshots.includes(snapshot_id)) throw new ProductError(CODES.notFound, "That snapshot does not belong to this project.");
    const q = text(question);
    if (!q) throw new ProductError(CODES.question, "Ask a question to analyse.");

    const base = this.#fx.view;
    const isDemoSnapshot = project_id === base.project.id && snapshot_id === base.snapshot.id;
    const n = this.#seq++;
    const result_id = `demo-result-${pad(100 + n)}`;
    // evidence visible to this snapshot: the packaged one for the demo snapshot, none for a new import
    const evidence = isDemoSnapshot ? clone(base.evidence) : [];
    // OBSERVED_IN only for evidence observed in the analyzed snapshot — never attached wholesale
    const observed = isDemoSnapshot ? observedIds(base) : new Set<string>();
    const snapNode: GraphNode = { id: `node-snapshot-${pad(100 + n)}`, kind: "Snapshot", source_id: snapshot_id, visibility: base.visibility };
    const evNodes: GraphNode[] = evidence.map((e, i) => ({ id: `node-evidence-${pad(100 + n)}-${i + 1}`, kind: "EvidenceItem", source_id: e.id, visibility: e.visibility }));
    const turn: Turn = {
      id: `demo-turn-${pad(100 + n)}`, snapshot_id, result_id, runtime: clone(base.runtime), question: q,
      answer: "Deterministic mock: no model was run, and the visible evidence does not carry this question.",
      // the demo has no valid counterfactual pair in the same family/provenance
      abstained: true, visible_evidence_ids: evidence.map(e => e.id), missing_evidence: [COUNTERFACTUAL_MARKER],
    };
    const view: ProductView = {
      ...clone(base),
      project: isDemoSnapshot ? clone(base.project) : { id: project_id, display_name: project_id },
      snapshot: isDemoSnapshot ? clone(base.snapshot) : { id: snapshot_id, baseline_id: snapshot_id, sha256: "", metric_definitions: [], metric_changes: [] },
      result: {
        ...clone(base.result), id: result_id, status: "complete", disposition: "abstain", causal_status: "unverified",
        summary: "Deterministic mock result. No claim was made for this question.",
        denominators: { independent_families: null, contexts: evidence.length ? 1 : null, outputs: 1, claims: 0 },
      },
      conversation: { id: `demo-conversation-${pad(100 + n)}`, turns: [turn] },
      claims: [],
      evidence,
      graph: {
        nodes: [snapNode, ...evNodes],
        edges: evNodes.filter(e => observed.has(e.source_id))
          .map((e, i) => ({ id: `edge-observed-${pad(100 + n)}-${i + 1}`, kind: "OBSERVED_IN", source: e.id, target: snapNode.id, visibility: e.visibility })),
      },
    };
    this.#results.set(result_id, clone(view));
    return view;
  }

  /* ------------------------------------------------------------------- 6 */
  async follow_up(result_id: string, selection_kind: SelectionKind, selection_id: string, question: string): Promise<ProductView> {
    if (typeof result_id !== "string" || !RESULT_ID.test(result_id)) throw new ProductError(CODES.invalidId, "The result ID is not well formed.");
    const prior = this.#result(result_id);
    if (selection_kind !== "claim" && selection_kind !== "node")
      throw new ProductError(CODES.selectionKind, "selection_kind must be exactly claim or node.");
    const problem = questionProblem(question);
    if (problem) throw new ProductError(CODES.question, problem);

    // the selection must resolve exactly inside the immutable parent
    const missing = (): never => { throw new ProductError(CODES.selectionMissing, "The selection does not resolve in this result."); };
    let base: Claim | undefined, evidenceId: string | undefined;
    if (selection_kind === "claim") {
      base = prior.claims.find(c => c.id === selection_id) ?? missing();
    } else {
      const node: GraphNode = prior.graph.nodes.find(x => x.id === selection_id) ?? missing();
      if (node.kind === "AtomicClaim") base = prior.claims.find(c => c.id === node.source_id) ?? missing();
      else if (node.kind === "EvidenceItem") evidenceId = prior.evidence.some(e => e.id === node.source_id) ? node.source_id : missing();
      else if (node.kind !== "Snapshot" && node.kind !== "Disposition") missing();
    }

    // replay of the exact tuple returns the same child, same result ID and turn ID
    const key = JSON.stringify([result_id, selection_kind, selection_id, question]);
    const known = this.#children.get(key);
    if (known) return clone(this.#result(known));

    const n = this.#seq++;
    const id = pad(100 + n);
    const new_result = `demo-result-${id}`;
    const failed = prior.result.status === "technical_failure";
    // citation and counterevidence keep their roles and their parent order; only the turn's
    // visible scope is the sorted union. Snapshot / Disposition nodes scope to nothing.
    const citation_ids = base ? [...base.citation_ids] : evidenceId ? [evidenceId] : [];
    const counterevidence_ids = base ? [...base.counterevidence_ids] : [];
    // Kiên, 2026-10-09: the new turn and the new claim inherit the parent's last turn missing_evidence
    // verbatim — the counterfactual marker included only if the parent had it, never added here.
    // A failed child carries no marker.
    const lastParent = prior.conversation.turns[prior.conversation.turns.length - 1];
    const inherited = lastParent ? [...lastParent.missing_evidence] : [];
    const missing_evidence = failed ? inherited.filter(m => m !== COUNTERFACTUAL_MARKER) : inherited;
    const turn: Turn = {
      id: `demo-turn-${id}`, snapshot_id: prior.snapshot.id, result_id: new_result, runtime: clone(prior.runtime), question,
      answer: failed
        ? "The parent run did not complete, so this follow-up cannot produce a finding either."
        : "Deterministic mock follow-up: no new evidence was read, so the record still does not carry this question.",
      abstained: true,
      visible_evidence_ids: [...new Set([...citation_ids, ...counterevidence_ids])].sort(),
      missing_evidence: [...missing_evidence],
    };
    const conversation = { ...clone(prior.conversation), turns: [...clone(prior.conversation.turns), turn] };

    let view: ProductView;
    if (failed) {
      const nodes = prior.graph.nodes.filter(x => x.kind === "Snapshot" || x.kind === "EvidenceItem");
      const ids = new Set(nodes.map(x => x.id));
      view = {
        ...clone(prior),
        result: { ...clone(prior.result), id: new_result, status: "technical_failure", disposition: null,
          denominators: { ...prior.result.denominators, claims: 0 } },
        conversation, claims: [],
        graph: { nodes: clone(nodes), edges: clone(prior.graph.edges.filter(e => e.kind === "OBSERVED_IN" && ids.has(e.source) && ids.has(e.target))) },
      };
    } else {
      const claim: Claim = {
        id: `demo-claim-${id}`, turn_id: turn.id,
        text: "This follow-up stays uncertain: the selected evidence does not settle the question.",
        claim_type: "uncertainty_statement", epistemic_state: "abstained", support: "not_estimated",
        citation_ids, counterevidence_ids, missing_evidence: [...missing_evidence], max_strength: "abstain",
      };
      const claims = [...clone(prior.claims), claim];
      // the Disposition node is scoped to this result: the parent's is replaced, never reused
      const kept = prior.graph.nodes.filter(x => x.kind !== "Disposition");
      const evNode = (eid: string) => prior.graph.nodes.find(x => x.kind === "EvidenceItem" && x.source_id === eid);
      const claimNode: GraphNode = { id: `node-claim-${id}`, kind: "AtomicClaim", source_id: claim.id, visibility: prior.visibility };
      const dispNode: GraphNode = { id: `node-disposition-${id}`, kind: "Disposition", source_id: new_result, visibility: prior.visibility };
      const cites = citation_ids.map(evNode).filter((x): x is GraphNode => !!x)
        .map((x, i) => ({ id: `edge-cites-${id}-${i + 1}`, kind: "CITES", source: claimNode.id, target: x.id, visibility: x.visibility }));
      const claimNodes = [...kept.filter(x => x.kind === "AtomicClaim"), claimNode];
      const assigned = claimNodes.map((x, i) => ({ id: `edge-disposition-${id}-${i + 1}`, kind: "ASSIGNED_DISPOSITION", source: x.id, target: dispNode.id, visibility: prior.visibility }));
      view = {
        ...clone(prior),
        result: {
          id: new_result, status: "complete", disposition: "abstain", causal_status: "unverified", ...FOLLOW_UP,
          denominators: { independent_families: null, contexts: prior.result.denominators.contexts, outputs: prior.result.denominators.outputs, claims: claims.length },
        },
        conversation, claims,
        graph: {
          nodes: [...clone(kept), claimNode, dispNode],
          edges: [...clone(prior.graph.edges.filter(e => e.kind !== "ASSIGNED_DISPOSITION")), ...cites, ...assigned],
        },
      };
    }
    this.#results.set(new_result, clone(view));
    this.#children.set(key, new_result);
    return view;
  }

  /* ------------------------------------------------------------------- 7 */
  async view(result_id: string): Promise<ProductView> {
    return clone(this.#result(result_id));
  }

  /* ------------------------------------------------------------------- 8 */
  /* K10 (Kiên, 2026-10-09): exactly json | markdown | pdf, matched strictly — no alias, no case
     folding — anything else is invalid_request. The service renders PDF; this demo port does not. */
  async export_report(result_id: string, format: ReportFormat): Promise<Uint8Array> {
    if (typeof result_id !== "string" || !RESULT_ID.test(result_id)) throw new ProductError(CODES.invalidId, "The result ID is not well formed.");
    if (format !== "json" && format !== "markdown" && format !== "pdf") throw new ProductError(CODES.request, "format must be exactly json, markdown or pdf.");
    const v = this.#result(result_id);
    if (format === "json") return new TextEncoder().encode(JSON.stringify(v, null, 2) + "\n");
    if (format === "markdown") return new TextEncoder().encode(reportMarkdown(v));
    throw new ProductError(CODES.unavailable, "The demo port does not render PDF.");
  }

  /* ------------------------------------------------------------------- 9 */
  async delete_project(project_id: string): Promise<DeleteReceipt> {
    const p = this.#projects.get(project_id);
    if (!p) throw new ProductError(CODES.notFound, "No such project.");
    if (p.deleted && p.receipt) return clone(p.receipt); // idempotent
    // count what this project owns, each object once
    const owned = new Set<string>(p.snapshots.map(s => `snapshot:${s}`));
    for (const [id, v] of this.#results) {
      if (v.project.id !== project_id) continue;
      owned.add(`result:${id}`);
      v.conversation.turns.forEach(t => owned.add(`turn:${t.id}`));
      v.claims.forEach(c => owned.add(`claim:${c.id}`));
      v.evidence.forEach(e => owned.add(`evidence:${e.id}`));
      v.graph.nodes.forEach(x => owned.add(`node:${x.id}`));
      v.graph.edges.forEach(x => owned.add(`edge:${x.id}`));
      this.#results.delete(id);
    }
    const receipt: DeleteReceipt = { project_id, status: STATUS.deleted, deleted_count: owned.size, retained_shared_count: 0 };
    p.deleted = true;
    p.receipt = receipt;
    return clone(receipt);
  }

  /* --------------------------------------------------------------- guards */
  #live(project_id: string): ProjectState {
    const p = this.#projects.get(project_id);
    if (!p || p.deleted) throw new ProductError(CODES.notFound, "No such project.");
    return p;
  }
  #result(result_id: string): ProductView {
    const v = this.#results.get(result_id);
    if (!v || this.#projects.get(v.project.id)?.deleted) throw new ProductError(CODES.notFound, "No such result.");
    return v;
  }
}
