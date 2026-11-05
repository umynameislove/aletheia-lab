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

export interface RunningWork { id: number; op: string; label?: string }

type Fence = ReturnType<typeof createFence>;

/** Runs async work inside the current scope. `done`/`fail` run only if that scope is still the one on
    screen; otherwise the outcome goes to the late list and touches nothing else. A key already in
    flight is not sent twice, so a double click or a second Enter cannot start a second operation. */
export function createTracker(fence: Fence) {
  let late: LateWork[] = [];
  let running: RunningWork[] = [];
  let lastId = 0;
  const inFlight = new Set<string>();
  const listeners = new Set<() => void>();
  const changed = () => listeners.forEach(l => l());
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    late: () => late,
    running: () => running,
    dismiss(id: number) { late = late.filter(w => w.id !== id); changed(); },
    /** Logout or account switch: nothing about the old scope stays in memory. */
    clear() { late = []; changed(); },
    async run<T>(op: string, work: () => Promise<T>, then: {
      done: (value: T) => void;
      fail: (error: unknown) => void;
      late?: (value: T) => LateWork["result"];
      always?: () => void;
      key?: string;
      label?: string;
      /** the code of an error, for the late list */
      codeOf?: (error: unknown) => string;
    }): Promise<void> {
      if (then.key && inFlight.has(then.key)) return;
      if (then.key) inFlight.add(then.key);
      const ticket = fence.ticket();
      const id = ++lastId;
      running = [...running, { id, op, label: then.label }];
      changed();
      const stop = () => {
        if (then.key) inFlight.delete(then.key);
        running = running.filter(w => w.id !== id);
        changed();
      };
      try {
        const value = await work();
        stop();
        if (fence.current(ticket)) then.done(value);
        else { late = [{ id, op, result: then.late?.(value) }, ...late].slice(0, 20); changed(); }
      } catch (error) {
        stop();
        if (fence.current(ticket)) then.fail(error);
        else { late = [{ id, op, error: then.codeOf?.(error) ?? "unavailable" }, ...late].slice(0, 20); changed(); }
      } finally {
        then.always?.();
      }
    },
  };
}
