import type { ProductView } from "./types";
import { reviewOf, reviewStats, type DemoState } from "./store";
import { port } from "./service";

/* Files are built from port.export_report — the same projection the screens read
   (PRODUCT_CONTRACT §2). The reviewer's own pass is local to this machine, so the UI adds it
   as a separate layer next to the service's bytes, never inside them. */

function download(name: string, bytes: BlobPart, mime: string) {
  const blob = new Blob([bytes], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

const reviewerPass = (v: ProductView, s: DemoState) => ({
  note: "Local to this machine. Not produced by the service and not part of p6-product-view/v1.",
  summary: reviewStats(s, v.claims.map(c => c.id)),
  claims: Object.fromEntries(v.claims.map(c => [c.id, { state: reviewOf(s, c.id), objection: s.notes[c.id] || null }])),
});

/** Throws ProductError from the port as-is; the caller shows its code and safe message.
    Q11: the file is export_report's bytes, untouched. */
export async function downloadJSON(v: ProductView) {
  download(`aletheia-p6-${v.result.id}.json`, await port.export_report(v.result.id, "json") as BlobPart, "application/json");
}

export async function downloadMarkdown(v: ProductView) {
  download(`aletheia-p6-${v.result.id}.md`, await port.export_report(v.result.id, "markdown") as BlobPart, "text/markdown; charset=utf-8");
}

export async function downloadPdf(v: ProductView) {
  download(`aletheia-p6-${v.result.id}.pdf`, await port.export_report(v.result.id, "pdf") as BlobPart, "application/pdf");
}

/** The reviewer's own pass, as its own file — never merged into the service's bytes. */
export function downloadReview(v: ProductView, s: DemoState) {
  download(`aletheia-p6-${v.result.id}-review.json`, JSON.stringify({ result_id: v.result.id, reviewer_pass: reviewerPass(v, s) }, null, 2), "application/json");
}
