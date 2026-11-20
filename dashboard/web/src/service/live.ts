/* LiveProductPort — the ten ProductService methods over the local dashboard bridge
   (src/aletheia_lab/dashboard/bridge.py). The bridge owns one ProductService(store_root) and
   forwards each call unchanged, so names, arguments, return values and error codes are the
   service's own. Nothing here falls back to the demo: a failure is shown as a ProductError.

   Arguments travel in the POST body, never in the URL, so a granted folder path never lands in
   history, logs or a deep link. Erasable TypeScript only, so the tests can import it as is.

   Request lifecycle (Q-06.03): every call has a deadline. Reads are deduplicated while in flight
   and retried a bounded number of times with jitter. Mutations are never retried here: without a
   receipt API the client cannot tell whether a lost call committed, so it says outcome_unknown. */

import type { ConfirmMapping, PreviewImportResult, ProductView } from "../types.ts";
import {
  ProductError,
  type ConfirmResult,
  type LocalDeleteReceipt,
  type ProductPort,
  type RefreshResult,
  type ReportFormat,
  type SelectionKind,
} from "./port.ts";
import { looksLikeProblem, PROBLEM_STATUS, readProblem } from "./hosted.ts";

export const BRIDGE_HEADER = "X-P6-Bridge";

interface FetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
  arrayBuffer(): Promise<ArrayBuffer>;
}
type Fetch = (input: string, init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal }) => Promise<FetchResponse>;

const UNREACHABLE = () => new ProductError("bridge_unavailable", "The local product service is not reachable.");
const MALFORMED = () => new ProductError("service_failure", "The product service sent an unreadable answer.");
const TIMED_OUT = () => new ProductError("deadline_exceeded", "The product service did not answer in time.");
const UNKNOWN_OUTCOME = () => new ProductError("outcome_unknown", "The answer was lost, so it is not known whether this took effect.");

/** Safe to repeat: no side effect in the service. */
const READS = new Set(["demo_view", "view", "export_report"]);
const READ_DEADLINE_MS = 30_000;
/** The bridge serves one call at a time, so a mutation may first wait behind another one. */
const MUTATION_DEADLINE_MS = 120_000;
const READ_ATTEMPTS = 3;

/* A proxy can answer before the service does. The status still says what to do next, so it is not
   flattened into one generic failure. Only these statuses: their meaning does not depend on the body.
   401 and 403 are not here: on their own they cannot tell expired from revoked from refused, and a
   wrong guess either keeps a revoked person's data on screen or wipes a draft for nothing. */
const BY_STATUS: Record<number, [code: string, message: string]> = {
  404: ["not_found", "This was not found."],
  409: ["revision_conflict", "This changed before your request reached it."],
  413: ["payload_too_large", "The request was too large."],
  429: ["rate_limited", "The service asked to slow down."],
  503: ["service_busy", "The service is not available right now."],
};
const UNCONFIRMED = () => new ProductError("access_unconfirmed", "The service refused this without a reason this app can trust.");

/** The error to show, and whether its code may be acted on. Two shapes are read, each in full:
    a hosted Problem (service/hosted.ts) and the local bridge's {code, safe_message}. A session code
    is believed only with the status it belongs to; anything else falls back to the status alone. */
function asProductError(body: unknown, status: number): { error: ProductError; trusted: boolean } {
  if (looksLikeProblem(body)) {
    const problem = readProblem(body, status);
    if (problem) return { error: problem, trusted: true };
  } else if (typeof body === "object" && body !== null) {
    const { code, safe_message } = body as { code?: unknown; safe_message?: unknown };
    if (typeof code === "string" && /^[a-z][a-z0-9_.-]{0,63}$/.test(code) && typeof safe_message === "string" && safe_message.length <= 256
      && (PROBLEM_STATUS[code] === undefined || PROBLEM_STATUS[code] === status)) {
      return { error: new ProductError(code, safe_message), trusted: true };
    }
  }
  if (status === 401 || status === 403) return { error: UNCONFIRMED(), trusted: false };
  const known = BY_STATUS[status];
  return { error: known ? new ProductError(known[0], known[1]) : MALFORMED(), trusted: false };
}

const realDelay = (attempt: number) => new Promise<void>(resolve => setTimeout(resolve, 250 * 2 ** attempt * (0.5 + Math.random())));

export class LiveProductPort implements ProductPort {
  readonly kind = "live" as const;
  readonly #fetch: Fetch;
  readonly #base: string;
  readonly #delay: (attempt: number) => Promise<void>;
  readonly #readDeadline: number;
  readonly #mutationDeadline: number;
  readonly #pending = new Map<string, Promise<unknown>>();
  /** the view reads now on the wire: nothing else is ever abandoned on the person's behalf */
  readonly #views = new Set<AbortController>();
  readonly #onAnswer?: (status: number, code: string | null) => void;

  constructor(options: { fetch: Fetch; base?: string; delay?: (attempt: number) => Promise<void>; readDeadlineMs?: number; mutationDeadlineMs?: number; onAnswer?: (status: number, code: string | null) => void }) {
    this.#fetch = options.fetch;
    this.#base = options.base ?? "/api/";
    this.#delay = options.delay ?? realDelay;
    this.#readDeadline = options.readDeadlineMs ?? READ_DEADLINE_MS;
    this.#mutationDeadline = options.mutationDeadlineMs ?? MUTATION_DEADLINE_MS;
    this.#onAnswer = options.onAnswer;
  }

  /* One request, its deadline running until `body` has read the whole answer: headers can arrive and
     the body stall (a proxy, a bridge dying mid-write), and that must end the same way as no answer. */
  async #attempt<T>(method: string, args: Record<string, unknown>, body: (response: FetchResponse) => Promise<T>): Promise<T> {
    const read = READS.has(method);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), read ? this.#readDeadline : this.#mutationDeadline);
    if (method === "view") this.#views.add(controller);
    const aborted = new Promise<never>((_, reject) => {
      controller.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
    aborted.catch(() => { /* only matters while a race below is waiting on it */ });
    try {
      let response;
      try {
        response = await Promise.race([this.#fetch(this.#base + method, {
          method: "POST",
          headers: { "Content-Type": "application/json", [BRIDGE_HEADER]: "1" },
          body: JSON.stringify(args),
          signal: controller.signal,
        }), aborted]);
      } catch {
        if (read) throw controller.signal.aborted ? TIMED_OUT() : UNREACHABLE();
        throw UNKNOWN_OUTCOME();
      }
      if (!response.ok) {
        let parsed: unknown = null;
        try { parsed = await Promise.race([response.json(), aborted]); } catch { /* not JSON, or it stalled: stays generic */ }
        const { error, trusted } = asProductError(parsed, response.status);
        this.#onAnswer?.(response.status, trusted ? error.code : null);
        // a gateway's own 5xx says nothing about whether the service had already committed the change
        if (!read && !trusted && response.status >= 500 && response.status !== 503) throw UNKNOWN_OUTCOME();
        throw error;
      }
      this.#onAnswer?.(response.status, null);
      try {
        return await Promise.race([body(response), aborted]);
      } catch (e) {
        if (e instanceof ProductError) throw e;
        if (controller.signal.aborted && read) throw TIMED_OUT();
        throw read ? MALFORMED() : UNKNOWN_OUTCOME();
      }
    } finally {
      clearTimeout(timer);
      this.#views.delete(controller);
    }
  }

  async #send<T>(method: string, args: Record<string, unknown>, body: (response: FetchResponse) => Promise<T>): Promise<T> {
    if (!READS.has(method)) return this.#attempt(method, args, body);
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.#attempt(method, args, body);
      } catch (e) {
        const again = e instanceof ProductError && e.code === "bridge_unavailable" && attempt + 1 < READ_ATTEMPTS;
        if (!again) throw e;
        await this.#delay(attempt);
      }
    }
  }

  async #json<T>(method: string, args: Record<string, unknown>): Promise<T> {
    if (READS.has(method)) {
      const key = method + JSON.stringify(args);
      const running = this.#pending.get(key);
      if (running) return running as Promise<T>;
      const fresh = this.#readJson<T>(method, args).finally(() => this.#pending.delete(key));
      this.#pending.set(key, fresh);
      return fresh;
    }
    return this.#readJson<T>(method, args);
  }

  /* A body cut off on the way: for a read nothing changed and it can be asked again, but a mutation
     the service accepted did take effect, and saying "nothing changed" would invite a second one. */
  #readJson<T>(method: string, args: Record<string, unknown>): Promise<T> {
    return this.#send(method, args, response => response.json() as Promise<T>);
  }

  /** The person has moved on from the result being opened: its read stops. Reads only: a mutation is
      never abandoned, because the service may already have done it and only its answer can say so. */
  abandonViews(): void {
    for (const controller of this.#views) controller.abort();
  }

  demo_view(): Promise<ProductView> {
    return this.#json("demo_view", {});
  }
  preview_import(root: string): Promise<PreviewImportResult> {
    return this.#json("preview_import", { root });
  }
  confirm_import(preview_id: string, mapping: ConfirmMapping): Promise<ConfirmResult> {
    return this.#json("confirm_import", { preview_id, mapping });
  }
  refresh(project_id: string): Promise<RefreshResult> {
    return this.#json("refresh", { project_id });
  }
  analyze_mock(project_id: string, snapshot_id: string, question: string): Promise<ProductView> {
    return this.#json("analyze_mock", { project_id, snapshot_id, question });
  }
  analyze_observations(project_id: string, snapshot_id: string, question: string): Promise<ProductView> {
    return this.#json("analyze_observations", { project_id, snapshot_id, question });
  }
  follow_up(result_id: string, selection_kind: SelectionKind, selection_id: string, question: string): Promise<ProductView> {
    return this.#json("follow_up", { result_id, selection_kind, selection_id, question });
  }
  view(result_id: string): Promise<ProductView> {
    return this.#json("view", { result_id });
  }
  /** The service's bytes, untouched (JOB Q11). */
  async export_report(result_id: string, format: ReportFormat): Promise<Uint8Array> {
    // never hand on part of a file as if it were the whole one
    return this.#send("export_report", { result_id, format }, async response => new Uint8Array(await response.arrayBuffer()));
  }
  delete_project(project_id: string): Promise<LocalDeleteReceipt> {
    return this.#json("delete_project", { project_id });
  }
}
