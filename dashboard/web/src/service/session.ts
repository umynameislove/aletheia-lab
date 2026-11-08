/* When access can change while a tab is open (Q-04, J-02). The server is the only authority: it refuses
   every request as soon as a session or membership is revoked. This guard decides what the client does
   about content already on screen, at the moments that matter, without polling every tab every few
   seconds: at 50k tabs a 5 s poll alone would be 10,000 requests per second.

   - Any answered request is already a check, so an active person causes no extra traffic.
   - Coming back to a tab, or restoring it from the back/forward cache, checks again. After a long
     absence the content stays covered until the answer arrives.
   - An interaction checks when the last confirmation is older than interactionStaleMs.
   - An idle visible tab checks about once a minute; a hidden tab does not check at all.
   - Tabs of one browser tell each other, so logout reaches them with no server traffic.

   Erasable TypeScript only, so the tests can import it as is. It holds no content and no credential. */

export type SessionState =
  | "active"       // confirmed recently enough; nothing covered
  | "confirming"   // covered until the server answers
  | "unreachable"  // covered: access could not be confirmed
  | "expired"      // covered: same person may sign in again and carry on, so memory is kept
  | "revoked"      // wiped: this person no longer has access
  | "switched";    // wiped: another account is signed in on this browser

export type SessionAnswer =
  | { status: "ok"; context_id: string }
  | { status: "expired" }
  | { status: "revoked" }
  | { status: "unreachable" };

/** What tabs tell each other. An opaque ID at most: never content, never a credential. */
export type PeerMessage =
  | { type: "confirmed"; context_id: string }
  | { type: "expired" }
  | { type: "revoked" }
  | { type: "switched"; context_id: string };

export const SESSION_TIMING = {
  /** hidden longer than this: cover the content until the server has answered */
  hiddenCoverMs: 30_000,
  /** an interaction checks if the last confirmation is older than this */
  interactionStaleMs: 5_000,
  /** an idle, visible tab checks this often, spread by jitter so tabs do not move in step */
  idleCheckMs: 60_000,
  jitter: 0.2,
  /** the longest wait between checks while the server cannot be reached */
  maxBackoffMs: 300_000,
} as const;

export function createSessionGuard(options: {
  check: () => Promise<SessionAnswer>;
  contextId: string;
  now: () => number;
  random?: () => number;
  /** tell the other tabs of this browser */
  tell?: (message: PeerMessage) => void;
  /** forget everything held for the old scope: caches, drafts, in-flight work */
  onWipe: () => void;
}) {
  const random = options.random ?? Math.random;
  let state: SessionState = "active";
  let contextId = options.contextId;
  let lastConfirmed = options.now();
  let failures = 0;
  let inFlight: Promise<void> | null = null;
  const listeners = new Set<() => void>();

  const spread = (ms: number) => ms * (1 - SESSION_TIMING.jitter + 2 * SESSION_TIMING.jitter * random());
  const nextInterval = () => Math.min(SESSION_TIMING.idleCheckMs * 2 ** failures, SESSION_TIMING.maxBackoffMs);
  let interval = spread(nextInterval());

  const set = (next: SessionState) => {
    if (next === state) return;
    state = next;
    listeners.forEach(l => l());
  };
  const terminal = () => state === "revoked" || state === "switched";
  const confirmedNow = () => {
    lastConfirmed = options.now();
    failures = 0;
    interval = spread(nextInterval());
  };

  /** Wiping happens once, before anything else can render or read. */
  const end = (next: "revoked" | "switched") => {
    if (terminal()) return;
    options.onWipe();
    set(next);
  };

  const settle = (answer: SessionAnswer) => {
    if (terminal()) return;
    switch (answer.status) {
      case "ok":
        if (answer.context_id !== contextId) {
          contextId = answer.context_id;
          options.tell?.({ type: "switched", context_id: answer.context_id });
          end("switched");
          return;
        }
        confirmedNow();
        options.tell?.({ type: "confirmed", context_id: contextId });
        set("active");
        return;
      case "expired":
        options.tell?.({ type: "expired" });
        set("expired");
        return;
      case "revoked":
        options.tell?.({ type: "revoked" });
        end("revoked");
        return;
      case "unreachable":
        failures += 1;
        interval = spread(nextInterval());
        // what was covered stays covered: not knowing is not the same as being allowed
        if (state === "confirming") set("unreachable");
    }
  };

  const check = (cover: boolean): Promise<void> => {
    if (terminal() || state === "expired") return Promise.resolve();
    if (cover) set("confirming");
    if (!inFlight) {
      inFlight = options.check()
        .catch((): SessionAnswer => ({ status: "unreachable" }))
        .then(settle)
        .finally(() => { inFlight = null; });
    }
    return inFlight;
  };

  return {
    state: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },

    /** Ask now. `cover` hides the content until the answer arrives. */
    check,

    /** The tab became visible again after this many milliseconds. */
    onVisible(hiddenMs: number) { return check(hiddenMs >= SESSION_TIMING.hiddenCoverMs); },

    /** The page came back from the back/forward cache: it may be from before a logout. */
    onRestored() { return check(true); },

    onOnline() { return check(state === "unreachable"); },

    /** A click or a key press. Hover and scrolling do not count: they would cost traffic for no decision. */
    onInteraction() {
      if (options.now() - lastConfirmed >= SESSION_TIMING.interactionStaleMs) return check(false);
      return Promise.resolve();
    },

    /** Whether the idle check is due. Hidden tabs answer false: they receive nothing new anyway. */
    due(visible: boolean) {
      return visible && !terminal() && state !== "expired" && !inFlight && options.now() - lastConfirmed >= interval;
    },

    /** Every answer from the service says something: success is a confirmation, 401 and 403 may end the
        session. `code` is null when the port could not trust the reason (service/live.ts). An untrusted
        refusal is not guessed into expired or revoked: the content is covered and the session asked. */
    noteResponse(status: number, code: string | null) {
      if (terminal()) return;
      if (status >= 200 && status < 300) {
        if (state === "active") confirmedNow();
      } else if (code === "session_revoked" || code === "membership_revoked") {
        settle({ status: "revoked" });
      } else if (code === "session_expired") {
        settle({ status: "expired" });
      } else if (status === 401 || (status === 403 && code === null)) {
        void check(true);
      }
    },

    onPeer(message: PeerMessage) {
      if (terminal()) return;
      if (message.type === "revoked") end("revoked");
      else if (message.type === "expired") set("expired");
      else if (message.context_id !== contextId) { contextId = message.context_id; end("switched"); }
      else if (message.type === "confirmed") {
        // another tab just asked, so this one need not; and if it was waiting, it can stop waiting
        confirmedNow();
        if (state === "confirming" || state === "unreachable") set("active");
      }
    },

    /** Signed in again. The same person carries on; anyone else starts clean. */
    resume(newContextId: string) {
      if (terminal()) return;
      settle({ status: "ok", context_id: newContextId });
    },
  };
}

export type SessionGuard = ReturnType<typeof createSessionGuard>;

const IDLE_TICK_MS = 5_000;
const CHANNEL = "aletheia-session";
const LOCK = "aletheia-session-check";

/* Connects a guard to the browser. Returns the way to disconnect it. */
export function attachSessionGuard(guard: SessionGuard, now: () => number = Date.now): () => void {
  let hiddenSince = document.visibilityState === "hidden" ? now() : null;
  const onVisibility = () => {
    if (document.visibilityState === "hidden") { hiddenSince = now(); return; }
    const away = hiddenSince === null ? 0 : now() - hiddenSince;
    hiddenSince = null;
    void guard.onVisible(away);
  };
  const onPageShow = (e: PageTransitionEvent) => { if (e.persisted) void guard.onRestored(); };
  const onOnline = () => { void guard.onOnline(); };
  const onAct = () => { void guard.onInteraction(); };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pageshow", onPageShow);
  window.addEventListener("online", onOnline);
  document.addEventListener("pointerdown", onAct, true);
  document.addEventListener("keydown", onAct, true);

  // only one tab of a browser does the timed check; where locks are missing, each tab checks for itself
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  const timer = window.setInterval(() => {
    if (!guard.due(document.visibilityState === "visible")) return;
    if (!locks) { void guard.check(false); return; }
    void locks.request(LOCK, { ifAvailable: true }, async lock => { if (lock) await guard.check(false); });
  }, IDLE_TICK_MS);

  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pageshow", onPageShow);
    window.removeEventListener("online", onOnline);
    document.removeEventListener("pointerdown", onAct, true);
    document.removeEventListener("keydown", onAct, true);
    window.clearInterval(timer);
  };
}

/** The channel between tabs, or nothing where the browser has none. */
export function sessionChannel(onMessage: (message: PeerMessage) => void) {
  if (typeof BroadcastChannel === "undefined") return { tell() {}, close() {} };
  const channel = new BroadcastChannel(CHANNEL);
  channel.onmessage = (e: MessageEvent<PeerMessage>) => onMessage(e.data);
  return { tell: (message: PeerMessage) => channel.postMessage(message), close: () => channel.close() };
}
