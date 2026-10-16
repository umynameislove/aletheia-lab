/* The single integration boundary, mirrored from aletheia_lab.product.service.ProductService
   (branch feat/p6-product-core-kien, commit 99f8ea46) and PRODUCT_CONTRACT.md §1.

   - Method names and parameters are the backend's, verbatim (snake_case), so a LiveProductPort
     can forward them one-to-one.
   - Every method is async: the demo port answers immediately, a live port will not.
   - Errors are ProductError(code, safe_message). safe_message never carries an absolute path,
     a raw exception, a secret, imported text or hidden truth — the UI shows remediation by code.

   This file is erasable TypeScript only (no enums, no parameter properties) so the contract tests
   can import the real modules under Node's type stripping. */

import type { ConfirmMapping, PreviewImportResult, ProductView } from "../types.ts";

export class ProductError extends Error {
  readonly code: string;
  readonly safe_message: string;
  constructor(code: string, safe_message: string) {
    super(`${code}: ${safe_message}`);
    this.name = "ProductError";
    this.code = code;
    this.safe_message = safe_message;
  }
}

/** confirm_import → "Dict gồm project_id, snapshot_id, status". */
export interface ConfirmResult {
  project_id: string;
  snapshot_id: string;
  status: string;
}

/** refresh → project_id, snapshot_id, status: new_snapshot | unchanged. The old snapshot never changes. */
export interface RefreshResult {
  project_id: string;
  snapshot_id: string;
  status: "new_snapshot" | "unchanged";
}

/** delete_project → content-free receipt. Idempotent; never deletes the source project. */
export interface DeleteReceipt {
  project_id: string;
  status: string;
  deleted_count: number;
  retained_shared_count: number;
}

export type ReportFormat = "json" | "markdown" | "pdf";
export type SelectionKind = "claim" | "node";

export interface ProductPort {
  /** Which implementation is answering — shown in the UI so a demo is never mistaken for live. */
  readonly kind: "demo" | "live";

  demo_view(): Promise<ProductView>;
  preview_import(root: string): Promise<PreviewImportResult>;
  confirm_import(preview_id: string, mapping: ConfirmMapping): Promise<ConfirmResult>;
  refresh(project_id: string): Promise<RefreshResult>;
  analyze_mock(project_id: string, snapshot_id: string, question: string): Promise<ProductView>;
  follow_up(result_id: string, selection_kind: SelectionKind, selection_id: string, question: string): Promise<ProductView>;
  view(result_id: string): Promise<ProductView>;
  export_report(result_id: string, format: ReportFormat): Promise<Uint8Array>;
  delete_project(project_id: string): Promise<DeleteReceipt>;
}

export const isProductError = (e: unknown): e is ProductError =>
  e instanceof ProductError || (typeof e === "object" && e !== null && (e as { name?: string }).name === "ProductError");
