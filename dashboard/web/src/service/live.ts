/* LiveProductPort — the nine ProductService methods over the local dashboard bridge
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
  type DeleteReceipt,
  type ProductPort,
  type RefreshResult,
  type ReportFormat,
  type SelectionKind,
} from "./port.ts";

export const BRIDGE_HEADER = "X-P6-Bridge";

type Fetch = (input: string, init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
  arrayBuffer(): Promise<ArrayBuffer>;
}>;

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

/** Only a well-formed {code, safe_message} becomes the error shown; anything else is generic. */
function asProductError(body: unknown): ProductError {
  if (typeof body === "object" && body !== null) {
    const { code, safe_message } = body as { code?: unknown; safe_message?: unknown };
    if (typeof code === "string" && /^[a-z][a-z0-9_.-]{0,63}$/.test(code) && typeof safe_message === "string" && safe_message.length <= 256) {
      return new ProductError(code, safe_message);
    }
  }
  return MALFORMED();
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

  constructor(options: { fetch: Fetch; base?: string; delay?: (attempt: number) => Promise<void>; readDeadlineMs?: number; mutationDeadlineMs?: number }) {
    this.#fetch = options.fetch;
    this.#base = options.base ?? "/api/";
    this.#delay = options.delay ?? realDelay;
    this.#readDeadline = options.readDeadlineMs ?? READ_DEADLINE_MS;
    this.#mutationDeadline = options.mutationDeadlineMs ?? MUTATION_DEADLINE_MS;
  }

  async #attempt(method: string, args: Record<string, unknown>) {
    const read = READS.has(method);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), read ? this.#readDeadline : this.#mutationDeadline);
    let response;
    try {
      response = await this.#fetch(this.#base + method, {
        method: "POST",
        headers: { "Content-Type": "application/json", [BRIDGE_HEADER]: "1" },
        body: JSON.stringify(args),
        signal: controller.signal,
      });
    } catch {
      if (read) throw controller.signal.aborted ? TIMED_OUT() : UNREACHABLE();
      throw UNKNOWN_OUTCOME();
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) {
      let body: unknown = null;
      try { body = await response.json(); } catch { /* not JSON: stays generic */ }
      throw asProductError(body);
    }
    return response;
  }

  async #send(method: string, args: Record<string, unknown>) {
    if (!READS.has(method)) return this.#attempt(method, args);
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.#attempt(method, args);
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

  async #readJson<T>(method: string, args: Record<string, unknown>): Promise<T> {
    const response = await this.#send(method, args);
    try {
      return (await response.json()) as T;
    } catch {
      throw MALFORMED();
    }
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
  follow_up(result_id: string, selection_kind: SelectionKind, selection_id: string, question: string): Promise<ProductView> {
    return this.#json("follow_up", { result_id, selection_kind, selection_id, question });
  }
  view(result_id: string): Promise<ProductView> {
    return this.#json("view", { result_id });
  }
  /** The service's bytes, untouched (JOB Q11). */
  async export_report(result_id: string, format: ReportFormat): Promise<Uint8Array> {
    const response = await this.#send("export_report", { result_id, format });
    return new Uint8Array(await response.arrayBuffer());
  }
  delete_project(project_id: string): Promise<DeleteReceipt> {
    return this.#json("delete_project", { project_id });
  }
}
