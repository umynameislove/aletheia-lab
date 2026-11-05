/* The client boundary (Q-06): JSON from the service is untrusted until it is checked here, at run
   time, for version, shape and scope. A TypeScript cast proves nothing about a response.

   - Fails closed with a ProductError and never substitutes another view. No demo fallback.
   - Checks what this client reads. It does not re-derive the service's semantics: delta,
     adverse_status and citation choice stay the service's.
   - `checkedPort` wraps any ProductPort, so the raw transport stays testable on its own.

   Erasable TypeScript only, so the tests can import it as is. */

import type { PreviewImportResult, ProductView } from "../types.ts";
import { ProductError, type ConfirmResult, type DeleteReceipt, type ProductPort, type RefreshResult } from "./port.ts";

export const PRODUCT_VIEW_SCHEMA = "p6-product-view/v1";

const UNSUPPORTED = () => new ProductError("schema_unsupported", "This version of the app cannot read that answer from the service.");
const MALFORMED = (where: string) => new ProductError("response_invalid", `The service sent an answer this app cannot trust (${where}).`);
const OUT_OF_SCOPE = () => new ProductError("response_out_of_scope", "The service answered about something other than what was asked, so it was not shown.");

type Obj = Record<string, unknown>;

const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
const isStr = (x: unknown): x is string => typeof x === "string";
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const isBool = (x: unknown): x is boolean => typeof x === "boolean";
const isStrList = (x: unknown): x is string[] => Array.isArray(x) && x.every(isStr);
const nullableNum = (x: unknown) => x === null || isNum(x);

function need(ok: boolean, where: string): asserts ok {
  if (!ok) throw MALFORMED(where);
}

const uniqueIds = (rows: { id: string }[]) => new Set(rows.map(r => r.id)).size === rows.length;

function checkRuntime(x: unknown, where: string) {
  need(isObj(x) && isStr(x.provider) && isStr(x.model) && isBool(x.external_call), where);
}

function checkObservation(x: unknown) {
  if (x === null) return;
  need(isObj(x) && isStr(x.snapshot_id) && isStr(x.run_id) && (x.step === null || isNum(x.step)) && isNum(x.value), "metric observation");
}

/** version → shape → references. Throws; returns the same object it was given. */
export function checkProductView(x: unknown): ProductView {
  need(isObj(x), "view");
  if (x.schema_version !== PRODUCT_VIEW_SCHEMA) throw UNSUPPORTED();
  need(isBool(x.demo_only) && isStr(x.mode) && isStr(x.visibility), "view header");
  need(isObj(x.project) && isStr(x.project.id) && isStr(x.project.display_name), "project");
  checkRuntime(x.runtime, "runtime");

  const snapshot = x.snapshot;
  need(isObj(snapshot) && isStr(snapshot.id) && isStr(snapshot.baseline_id) && isStr(snapshot.sha256), "snapshot");
  need(Array.isArray(snapshot.metric_definitions) && Array.isArray(snapshot.metric_changes), "snapshot metrics");
  for (const d of snapshot.metric_definitions as unknown[]) {
    need(isObj(d) && isStr(d.metric_name) && isStr(d.direction) && isNum(d.regression_threshold), "metric definition");
  }
  for (const c of snapshot.metric_changes as unknown[]) {
    need(isObj(c) && isStr(c.evidence_id) && isStr(c.metric_name) && isStr(c.kind) && isStr(c.adverse_status) && nullableNum(c.delta), "metric change");
    checkObservation(c.before);
    checkObservation(c.after);
  }

  const result = x.result;
  need(isObj(result) && isStr(result.id) && isStr(result.status) && isStr(result.causal_status), "result");
  need(result.disposition === null || isStr(result.disposition), "disposition");
  need(isStr(result.summary) && isStr(result.allowed_wording) && isStr(result.forbidden_wording) && isStr(result.caveat), "result wording");
  need(isObj(result.denominators), "denominators");
  for (const k of ["independent_families", "contexts", "outputs", "claims"]) need(nullableNum(result.denominators[k]), "denominators");

  const conversation = x.conversation;
  need(isObj(conversation) && isStr(conversation.id) && Array.isArray(conversation.turns), "conversation");
  const turns = conversation.turns as { id: string; visible_evidence_ids: string[] }[];
  for (const t of turns as unknown[]) {
    need(isObj(t) && isStr(t.id) && isStr(t.snapshot_id) && isStr(t.result_id) && isStr(t.question) && isStr(t.answer) && isBool(t.abstained), "turn");
    need(isStrList(t.visible_evidence_ids) && isStrList(t.missing_evidence), "turn evidence");
    checkRuntime(t.runtime, "turn runtime");
  }
  need(uniqueIds(turns), "turn ids");

  need(Array.isArray(x.evidence), "evidence");
  const evidence = x.evidence as { id: string }[];
  for (const e of evidence as unknown[]) {
    need(isObj(e) && isStr(e.id) && isStr(e.kind) && isStr(e.title) && isStr(e.text) && isStr(e.source_sha256) && isStr(e.visibility) && isBool(e.redacted), "evidence item");
    need(isObj(e.reproduction_ref) && isStr(e.reproduction_ref.record_id) && isStr(e.reproduction_ref.record_kind), "evidence reference");
  }
  need(uniqueIds(evidence), "evidence ids");

  need(Array.isArray(x.claims), "claims");
  const claims = x.claims as { id: string }[];
  const known = new Set(evidence.map(e => e.id));
  const turnIds = new Set(turns.map(t => t.id));
  for (const c of claims as unknown[]) {
    need(isObj(c) && isStr(c.id) && isStr(c.turn_id) && isStr(c.text) && isStr(c.claim_type) && isStr(c.epistemic_state) && isStr(c.support) && isStr(c.max_strength), "claim");
    need(isStrList(c.citation_ids) && isStrList(c.counterevidence_ids) && isStrList(c.missing_evidence), "claim evidence");
    // a citation has to lead somewhere, or the reader would show a relation it cannot back up
    need(c.citation_ids.every(id => known.has(id)) && c.counterevidence_ids.every(id => known.has(id)), "claim citation");
    need(turnIds.has(c.turn_id), "claim turn");
  }
  need(uniqueIds(claims), "claim ids");

  const graph = x.graph;
  need(isObj(graph) && Array.isArray(graph.nodes) && Array.isArray(graph.edges), "graph");
  for (const n of graph.nodes as unknown[]) need(isObj(n) && isStr(n.id) && isStr(n.kind) && isStr(n.source_id), "graph node");
  for (const e of graph.edges as unknown[]) need(isObj(e) && isStr(e.id) && isStr(e.kind) && isStr(e.source) && isStr(e.target), "graph edge");

  return x as unknown as ProductView;
}

function checkIssues(x: unknown) {
  need(Array.isArray(x), "issues");
  for (const i of x as unknown[]) {
    need(isObj(i) && isStr(i.code) && isStr(i.severity) && isStr(i.stage) && isStr(i.message) && isNum(i.occurrences), "issue");
    need(i.relative_path === null || isStr(i.relative_path), "issue path");
  }
}

/** preview_import carries no version field yet, so its shape is the only thing to check (open with K-02). */
export function checkPreview(x: unknown): PreviewImportResult {
  need(isObj(x) && isStr(x.preview_id), "preview");
  for (const k of ["included_count", "redacted_count", "excluded_count", "withheld_count"]) need(isNum(x[k]), "preview counts");
  checkIssues(x.blockers);
  checkIssues(x.warnings);
  need(isStrList(x.outbound_categories), "outbound categories");
  const c = x.mapping_candidates;
  need(isObj(c), "mapping candidates");
  need(Array.isArray(c.targets) && Array.isArray(c.metric_sources) && Array.isArray(c.configs) && Array.isArray(c.runs), "mapping candidates");
  for (const t of c.targets as unknown[]) need(isObj(t) && isStr(t.project_item_id) && isStr(t.label) && isBool(t.available) && isStrList(t.field_names), "target candidate");
  for (const m of c.metric_sources as unknown[]) {
    need(isObj(m) && isStr(m.project_item_id) && isStr(m.label) && isBool(m.available) && isStr(m.format), "metric source candidate");
    need(isStrList(m.field_names) && isStrList(m.run_id_field_candidates) && isStrList(m.metric_name_candidates), "metric source fields");
    need(Array.isArray(m.records_path_candidates) && m.records_path_candidates.every(isStrList), "records paths");
  }
  for (const r of c.runs as unknown[]) {
    need(isObj(r) && isStr(r.run_id) && isBool(r.available) && isStrList(r.source_metric_item_ids) && isStrList(r.available_config_item_ids), "run candidate");
  }
  return x as unknown as PreviewImportResult;
}

export function checkConfirm(x: unknown): ConfirmResult {
  need(isObj(x) && isStr(x.project_id) && isStr(x.snapshot_id) && isStr(x.status), "confirm");
  return x as unknown as ConfirmResult;
}

export function checkRefresh(x: unknown, project_id: string): RefreshResult {
  need(isObj(x) && isStr(x.project_id) && isStr(x.snapshot_id) && (x.status === "new_snapshot" || x.status === "unchanged"), "refresh");
  if (x.project_id !== project_id) throw OUT_OF_SCOPE();
  return x as unknown as RefreshResult;
}

export function checkDelete(x: unknown, project_id: string): DeleteReceipt {
  need(isObj(x) && isStr(x.project_id) && isStr(x.status) && isNum(x.deleted_count) && isNum(x.retained_shared_count), "delete receipt");
  if (x.project_id !== project_id) throw OUT_OF_SCOPE();
  return x as unknown as DeleteReceipt;
}

/** The view the caller asked about, or nothing: a stored result can never come back as sample data. */
function stored(x: unknown): ProductView {
  const view = checkProductView(x);
  if (view.demo_only) throw OUT_OF_SCOPE();
  return view;
}

/** The same nine methods, with every answer checked before anything can read it. */
export function checkedPort<P extends ProductPort>(inner: P): ProductPort {
  return {
    kind: inner.kind,
    demo_view: async () => {
      const view = checkProductView(await inner.demo_view());
      if (!view.demo_only) throw OUT_OF_SCOPE();
      return view;
    },
    preview_import: async root => checkPreview(await inner.preview_import(root)),
    confirm_import: async (preview_id, mapping) => checkConfirm(await inner.confirm_import(preview_id, mapping)),
    refresh: async project_id => checkRefresh(await inner.refresh(project_id), project_id),
    analyze_mock: async (project_id, snapshot_id, question) => {
      const view = stored(await inner.analyze_mock(project_id, snapshot_id, question));
      if (view.project.id !== project_id || view.snapshot.id !== snapshot_id) throw OUT_OF_SCOPE();
      return view;
    },
    follow_up: async (result_id, selection_kind, selection_id, question) => {
      const view = stored(await inner.follow_up(result_id, selection_kind, selection_id, question));
      // a follow-up child keeps its parent's turns under the parent's own result_id
      if (!view.conversation.turns.some(t => t.result_id === result_id)) throw OUT_OF_SCOPE();
      return view;
    },
    view: async result_id => {
      const view = stored(await inner.view(result_id));
      if (view.result.id !== result_id) throw OUT_OF_SCOPE();
      return view;
    },
    export_report: (result_id, format) => inner.export_report(result_id, format),
    delete_project: async project_id => checkDelete(await inner.delete_project(project_id), project_id),
  };
}
