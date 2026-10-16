/* export_report renderer for the demo port. Pure: no React, no DOM, no reviewer state.
   It renders the same projection the UI reads (PRODUCT_CONTRACT §2) and carries scope, caveat,
   IDs and citations — never a secret, a root path or an evaluator-only field. */

import type { MetricObservation, ProductView } from "../types.ts";
import { COUNTERFACTUAL_MARKER } from "./markers.ts";

const n = (x: number | null) => (x === null ? "not estimated (null — not zero)" : String(x));
/* the counterfactual marker is a status, so it is reported on its own line, never as a request */
const requests = (m: string[]) => m.filter(x => x !== COUNTERFACTUAL_MARKER).join("; ") || "none";
const counterfactual = (m: string[]) => (m.includes(COUNTERFACTUAL_MARKER) ? "not_available (service marker)" : "no marker — not evaluated here");
const obs = (o: MetricObservation | null) => (o === null ? "—" : String(o.value));

export function reportMarkdown(v: ProductView): string {
  const d = v.result.denominators;
  const node = (id: string) => v.graph.nodes.find(x => x.id === id)?.source_id ?? id;
  return [
    `# Audit record — ${v.project.display_name}`,
    "",
    `    result      ${v.result.id}`,
    `    status      ${v.result.status}`,
    `    snapshot    ${v.snapshot.id}   (baseline ${v.snapshot.baseline_id})`,
    `    snapshot sha256  ${v.snapshot.sha256}`,
    `    mode        ${v.mode}          visibility ${v.visibility}`,
    `    provider    ${v.runtime.provider} (external_call ${v.runtime.external_call})`,
    "",
    "## Disposition",
    "",
    `**${v.result.disposition ?? "null"}** · causal_status ${v.result.causal_status}`,
    "",
    v.result.summary,
    "",
    "## Wording limits",
    "",
    `- Allowed: ${v.result.allowed_wording}`,
    `- Forbidden: ${v.result.forbidden_wording}`,
    `- Caveat: ${v.result.caveat}`,
    "",
    "## Denominators",
    "",
    "| track | value |",
    "|---|---|",
    `| independent_families | ${n(d.independent_families)} |`,
    `| contexts | ${n(d.contexts)} |`,
    `| outputs | ${n(d.outputs)} |`,
    `| claims | ${n(d.claims)} |`,
    "",
    "These four are never pooled into one N.",
    "",
    "## Conversation",
    "",
    v.conversation.turns.length
      ? v.conversation.turns.map(t => [
        `### ${t.id}`, "",
        `- Result: ${t.result_id}`,
        `- Question: ${t.question}`,
        `- Answer: ${t.answer}`,
        `- Abstained: ${t.abstained}`,
        `- Visible evidence: ${t.visible_evidence_ids.join(", ") || "none"}`,
        `- Requested evidence not supplied: ${requests(t.missing_evidence)}`,
        `- Counterfactual comparison: ${counterfactual(t.missing_evidence)}`,
      ].join("\n")).join("\n\n")
      : "No turns.",
    "",
    "## Claims",
    "",
    v.claims.length ? v.claims.map(c => [
      `### ${c.id}`, "", c.text, "",
      `- claim_type: ${c.claim_type}`,
      `- epistemic_state: ${c.epistemic_state}`,
      `- support: ${c.support}`,
      `- max_strength: ${c.max_strength}`,
      `- citations: ${c.citation_ids.join(", ") || "none"}`,
      `- counterevidence: ${c.counterevidence_ids.join(", ") || "none"}`,
      `- requested evidence not supplied: ${requests(c.missing_evidence)}`,
      `- counterfactual comparison: ${counterfactual(c.missing_evidence)}`,
    ].join("\n")).join("\n\n") : "None in this view.",
    "",
    "## Evidence",
    "",
    v.evidence.length ? v.evidence.map(e => [
      `### ${e.id}`, "",
      `- title: ${e.title}`,
      `- kind: ${e.kind}`,
      `- text: ${e.text}`,
      `- reproduction_ref: ${e.reproduction_ref.record_kind} ${e.reproduction_ref.record_id}`,
      `- source_sha256: ${e.source_sha256}`,
      `- visibility: ${e.visibility} · redacted: ${e.redacted}`,
    ].join("\n")).join("\n\n") : "None in this view.",
    "",
    "## Metric changes",
    "",
    v.snapshot.metric_changes.length ? [
      "| evidence | metric | run | step | before | after | delta | kind | adverse_status |",
      "|---|---|---|---|---|---|---|---|---|",
      ...v.snapshot.metric_changes.map(c => {
        const o = c.after ?? c.before;
        return `| ${c.evidence_id} | ${c.metric_name} | ${o?.run_id ?? "—"} | ${o?.step ?? "null"} | ${obs(c.before)} | ${obs(c.after)} | ${c.delta ?? "null"} | ${c.kind} | ${c.adverse_status} |`;
      }),
      "",
      `before is read in ${v.snapshot.baseline_id}, after in ${v.snapshot.id}; each row is one (run, metric, step). adverse_status is the service's own.`,
    ].join("\n") : "None in this snapshot.",
    "",
    "## Graph",
    "",
    v.graph.edges.map(e => `- ${node(e.source)} —${e.kind}→ ${node(e.target)}`).join("\n") || "- no edges",
    "",
    "No edge type in this vocabulary means cause.",
    "",
  ].join("\n");
}
