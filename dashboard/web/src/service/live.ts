/* LiveProductPort — the nine ProductService methods over the local dashboard bridge
   (src/aletheia_lab/dashboard/bridge.py). The bridge owns one ProductService(store_root) and
   forwards each call unchanged, so names, arguments, return values and error codes are the
   service's own. Nothing here falls back to the demo: a failure is shown as a ProductError.

   Arguments travel in the POST body, never in the URL, so a granted folder path never lands in
   history, logs or a deep link. Erasable TypeScript only, so the tests can import it as is. */

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

type Fetch = (input: string, init: { method: string; headers: Record<string, string>; body: string }) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
  arrayBuffer(): Promise<ArrayBuffer>;
}>;

const UNREACHABLE = () => new ProductError("bridge_unavailable", "The local product service is not reachable.");
const MALFORMED = () => new ProductError("service_failure", "The product service sent an unreadable answer.");

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

export class LiveProductPort implements ProductPort {
  readonly kind = "live" as const;
  readonly #fetch: Fetch;
  readonly #base: string;

  constructor(options: { fetch: Fetch; base?: string }) {
    this.#fetch = options.fetch;
    this.#base = options.base ?? "/api/";
  }

  async #send(method: string, args: Record<string, unknown>) {
    let response;
    try {
      response = await this.#fetch(this.#base + method, {
        method: "POST",
        headers: { "Content-Type": "application/json", [BRIDGE_HEADER]: "1" },
        body: JSON.stringify(args),
      });
    } catch {
      throw UNREACHABLE();
    }
    if (!response.ok) {
      let body: unknown = null;
      try { body = await response.json(); } catch { /* not JSON: stays generic */ }
      throw asProductError(body);
    }
    return response;
  }

  async #json<T>(method: string, args: Record<string, unknown>): Promise<T> {
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
