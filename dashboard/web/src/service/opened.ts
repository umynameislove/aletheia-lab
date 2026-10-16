/* The project the person is working on, kept across reloads in Live (service owner, 2026-10-11):
   after confirm_import keep the returned project_id and snapshot_id; after analyze_mock/follow_up
   the result_id, which view() rehydrates. Opaque IDs only — never store_root, a source path, a
   mapping payload, imported content or a secret. The contract has no project listing, so this is
   "continue the project you had open", not a picker. The demo keeps everything in memory. */

export interface OpenedProject {
  project_id: string;
  snapshot_id: string;
  result_id?: string;
}

const KEY = "p6.opened";
const ID = /^[a-z0-9][a-z0-9_.-]{0,159}$/;

const valid = (o: unknown): o is OpenedProject => {
  if (typeof o !== "object" || o === null) return false;
  const { project_id, snapshot_id, result_id } = o as Record<string, unknown>;
  return typeof project_id === "string" && ID.test(project_id)
    && typeof snapshot_id === "string" && ID.test(snapshot_id)
    && (result_id === undefined || (typeof result_id === "string" && ID.test(result_id)));
};

/** Storage can be missing or blocked; every access is guarded and nothing depends on it. */
function storage(): Storage | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}

export function openedProject(): OpenedProject | null {
  try {
    const raw = storage()?.getItem(KEY);
    const o: unknown = raw ? JSON.parse(raw) : null;
    if (!valid(o)) return null;
    return { project_id: o.project_id, snapshot_id: o.snapshot_id, ...(o.result_id ? { result_id: o.result_id } : {}) };
  } catch {
    return null;
  }
}

/** Keeps only the three ID fields, whatever else the caller passed in. */
export function rememberProject(o: OpenedProject): void {
  if (!valid(o)) return;
  const keep: OpenedProject = { project_id: o.project_id, snapshot_id: o.snapshot_id, ...(o.result_id ? { result_id: o.result_id } : {}) };
  try { storage()?.setItem(KEY, JSON.stringify(keep)); } catch { /* not kept: the URL still carries the IDs */ }
}

export function forgetProject(project_id: string): void {
  if (openedProject()?.project_id !== project_id) return;
  try { storage()?.removeItem(KEY); } catch { /* nothing to forget */ }
}
