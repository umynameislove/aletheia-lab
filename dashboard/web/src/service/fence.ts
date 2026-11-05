/* Context fencing (Q-06.02). A response belongs to the scope it was asked in: account, workspace,
   project, snapshot, result and the screen that started it. After every await the caller asks
   whether that scope is still the one on screen. If not, the answer must not touch the URL, the
   active result, a toast or a draft. AbortController cannot do this: a mutation may already have
   committed, and an aborted fetch says nothing about that.

   account and workspace are null in the local app. They exist so hosted sessions (K-03/K-04) only
   have to supply them, not change every call site.

   Erasable TypeScript only, so the tests can import it as is. */

export interface Scope {
  account: string | null;
  workspace: string | null;
  project: string | null;
  snapshot: string | null;
  result: string | null;
  /** the screen, without its query: leaving it is leaving the operation */
  route: string;
}

export interface Ticket { epoch: number; scope: Scope }

const SAME_KEYS: (keyof Scope)[] = ["account", "workspace", "project", "result", "route"];

/** snapshot is left out on purpose: confirm and refresh move it forward inside one operation. */
export function sameScope(a: Scope, b: Scope): boolean {
  return SAME_KEYS.every(k => a[k] === b[k]);
}

export function createFence(read: () => Scope) {
  let epoch = 0;
  return {
    /** Taken before the await. */
    ticket(): Ticket { return { epoch, scope: read() }; },
    /** Asked after the await. False means: record it elsewhere, change nothing on screen. */
    current(t: Ticket): boolean { return t.epoch === epoch && sameScope(t.scope, read()); },
    /** A hard boundary that the URL cannot show: logout, account switch, revoke, reset. */
    advance(): void { epoch += 1; },
  };
}

/** Reads the scope from a location hash such as #/investigation?project=&snapshot=&result=. */
export function scopeFromHash(hash: string, identity: { account: string | null; workspace: string | null } = { account: null, workspace: null }): Scope {
  const [route, qs] = (hash || "#/").split("?");
  const q = new URLSearchParams(qs ?? "");
  return { ...identity, project: q.get("project"), snapshot: q.get("snapshot"), result: q.get("result"), route };
}

/** A finished operation the person had moved on from. It has a place to be found, and no other effect. */
export interface LateWork {
  id: number;
  op: string;
  /** set when the operation produced a result that can be opened */
  result?: { project_id: string; snapshot_id: string; result_id: string; question: string };
  error?: string;
}
