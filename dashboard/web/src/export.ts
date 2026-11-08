import type { ProductView } from "./types.ts";
import { reviewOf, reviewStats, type DemoState } from "./state.ts";
import { reportFilename } from "./data/format.ts";

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

export const reviewerPass = (v: ProductView, s: DemoState) => ({
  note: "Local to this machine. Not produced by the service and not part of p6-product-view/v1.",
  summary: reviewStats(s, v.claims.map(c => c.id)),
  // a kept objection belongs to the file only while the claim is disputed
  claims: Object.fromEntries(v.claims.map(c => {
    const state = reviewOf(s, c.id);
    return [c.id, { state, objection: state === "disputed" ? s.notes[c.id] || null : null }];
  })),
});

export type ReportKind = "json" | "markdown" | "pdf";

const KIND: Record<ReportKind, { ext: string; mime: string }> = {
  json: { ext: "json", mime: "application/json" },
  markdown: { ext: "md", mime: "text/markdown;charset=utf-8" },
  pdf: { ext: "pdf", mime: "application/pdf" },
};

/** The name a report of this result is saved under. */
export const reportName = (v: ProductView, kind: ReportKind) => reportFilename(v.project.display_name, v.result.id, KIND[kind].ext);

/** Saves the service's bytes untouched (Q11). The caller decides whether the scope is still current. */
export function saveReport(v: ProductView, kind: ReportKind, bytes: Uint8Array) {
  download(reportName(v, kind), bytes as BlobPart, KIND[kind].mime);
}

/** The reviewer's own pass, as its own file — never merged into the service's bytes. */
export function downloadReview(v: ProductView, s: DemoState) {
  download(reportFilename(v.project.display_name, v.result.id, "review.json"), JSON.stringify({ result_id: v.result.id, reviewer_pass: reviewerPass(v, s) }, null, 2), "application/json");
}
