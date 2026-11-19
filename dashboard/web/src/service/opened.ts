/* The project the person is working on, kept across reloads in Live (service owner, 2026-10-11):
   after confirm_import keep the returned project_id and snapshot_id; after analyze_observations/follow_up
   the result_id, which view() rehydrates. Opaque IDs only — never store_root, a source path, a
   mapping payload, imported content or a secret. The contract has no project listing, so this is
   "continue the project you had open", not a picker. The demo keeps everything in memory. */

export interface OpenedProject {
  project_id: string;
  snapshot_id: string;
  result_id?: string;
}

const KEY = "p6.opened";
/* What is stored carries its schema version. A version this build does not know is not read: never
   guessed at, and not overwritten until the person opens a project here. Entries from before versions
   had exactly these three fields, so they are still read, and take a version on the next write. */
const VERSION = 1;
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
    const version = (o as { v?: unknown }).v;
    if (version !== undefined && version !== VERSION) return null;
    return { project_id: o.project_id, snapshot_id: o.snapshot_id, ...(o.result_id ? { result_id: o.result_id } : {}) };
  } catch {
    return null;
  }
}

/** Keeps only the three ID fields, whatever else the caller passed in. */
export function rememberProject(o: OpenedProject): void {
  if (!valid(o)) return;
  const keep: OpenedProject = { project_id: o.project_id, snapshot_id: o.snapshot_id, ...(o.result_id ? { result_id: o.result_id } : {}) };
  try { storage()?.setItem(KEY, JSON.stringify({ v: VERSION, ...keep })); } catch { /* not kept: the URL still carries the IDs */ }
}

/** A confirm or a refresh changes the project on the service whatever screen is open when it answers,
    so its IDs are kept as soon as the answer arrives, not when (or whether) a screen shows it. */
export async function keptOnArrival<T extends OpenedProject>(work: Promise<T>, keep: (o: OpenedProject) => void = rememberProject): Promise<T> {
  const r = await work;
  keep({ project_id: r.project_id, snapshot_id: r.snapshot_id });
  return r;
}

export function forgetProject(project_id: string): void {
  if (openedProject()?.project_id !== project_id) return;
  try { storage()?.removeItem(KEY); } catch { /* nothing to forget */ }
}

/** Whoever this was, the next person on this browser does not continue their project. */
export function forgetAllProjects(): void {
  try { storage()?.removeItem(KEY); } catch { /* nothing to forget */ }
}
