import raw from "./p6_preview_import_v2.json";
import type { MetricSourceCandidate, PreviewImportResult } from "../types";

/* preview_import fixture locked with the service owner, byte-identical.
     v2  8e04f2e3…4c6    in use — K04d, adds metric_name_candidates (Kiên, commit 99f8ea46)
     v1  ac3a30a3…e9ccc  superseded; kept on disk for the record
   The JSON import widens string literals, so it is narrowed here; tests/contract.test.mjs
   proves the file really satisfies the contract with tsc. */

type RawSource = Omit<MetricSourceCandidate, "metric_name_candidates"> & { metric_name_candidates?: string[] };
const src = raw as unknown as Omit<PreviewImportResult, "mapping_candidates"> & {
  mapping_candidates: Omit<PreviewImportResult["mapping_candidates"], "metric_sources"> & { metric_sources: RawSource[] };
};

/** v2 carries metric_name_candidates on every metric source; v1 predates the field. */
export const PREVIEW_VERSION: "v1" | "v2" =
  src.mapping_candidates.metric_sources.every(m => Array.isArray(m.metric_name_candidates)) ? "v2" : "v1";

/* A v1 source has no candidate list, which is exactly the contract's "not resolved with
   certainty" case: [] — the UI then offers manual entry. Nothing is guessed. */
export const PREVIEW: PreviewImportResult = {
  ...src,
  mapping_candidates: {
    ...src.mapping_candidates,
    metric_sources: src.mapping_candidates.metric_sources.map(m => ({ ...m, metric_name_candidates: m.metric_name_candidates ?? [] })),
  },
};
export const CANDIDATES = PREVIEW.mapping_candidates;
