import type { ReviewState } from "./types.ts";
import { activateProject, projectDeleted } from "./service/transitions.ts";

/* ------------------------------------------------------- local demo state --
   The reviewer's own pass. Lives in memory on this machine, is never sent anywhere,
   and never changes what the service reported. */

export interface Receipt {
  scope: string;
  project_id: string;
  status: string;
  deleted_count: number;
  retained_shared_count: number;
}

export interface DemoState {
  activeProjectId?: string;
  review: Record<string, ReviewState>;
  notes: Record<string, string>;
  failed: boolean;
  /* UI state reflects the deletion receipt; Live deletion itself is performed by the port. */
  deletedConversation: boolean;
  deletedProject: boolean;
  receipt: Receipt | null;
}
export type Action =
  | { type: "review"; id: string; value: ReviewState }
  | { type: "reviewAll"; ids: string[] }
  | { type: "reviewReset" }
  | { type: "note"; id: string; text: string }
  | { type: "toggleFailed" }
  | { type: "deleteConversation"; receipt: Receipt }
  | { type: "deleteProject"; receipt: Receipt }
  | { type: "activateProject"; project_id: string }
  | { type: "reset" };

export const fresh = (): DemoState => ({ review: {}, notes: {}, failed: false, deletedConversation: false, deletedProject: false, receipt: null });

export function reduce(s: DemoState, a: Action): DemoState {
  switch (a.type) {
    // an objection is the person's own writing: changing the mark never deletes it. It shows, and is
    // exported, only while the claim is disputed; Reset review is the one place that clears it, after asking
    case "review": {
      const review = { ...s.review };
      if (a.value === "unchecked") delete review[a.id]; else review[a.id] = a.value;
      return { ...s, review };
    }
    case "reviewAll": {
      const review = { ...s.review };
      a.ids.forEach(id => { if (!review[id]) review[id] = "checked"; });
      return { ...s, review };
    }
    case "reviewReset": return { ...s, review: {}, notes: {} };
    case "note": return { ...s, notes: { ...s.notes, [a.id]: a.text } };
    case "toggleFailed": return { ...s, failed: !s.failed };
    case "deleteConversation": return { ...s, deletedConversation: true, receipt: a.receipt };
    case "deleteProject": return projectDeleted(s, a.receipt);
    case "activateProject": return activateProject(s, a.project_id);
    case "reset": return fresh();
  }
}

export const reviewOf = (s: DemoState, id: string): ReviewState => s.review[id] ?? "unchecked";
export function reviewStats(s: DemoState, ids: string[]) {
  let checked = 0, disputed = 0;
  ids.forEach(id => { const r = reviewOf(s, id); if (r === "checked") checked++; else if (r === "disputed") disputed++; });
  return { total: ids.length, checked, disputed, done: checked + disputed, left: ids.length - checked - disputed };
}
