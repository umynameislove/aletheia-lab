/* Numbers for reading (Q-08.02, Q-14.05). Formatting changes separators only: the value is never
   rounded, so a displayed number cannot cross a threshold the service did not report. null is
   unknown and stays visibly unknown, never 0. Delta and adverse_status are the service's, never recomputed. */

import type { AdverseStatus, MetricDefinition, MetricChange } from "../types.ts";

const FULL = { maximumFractionDigits: 20 } as const;

/** Grouping and decimal mark by locale, every significant digit kept. Values Intl would lose keep their exact form. */
export function formatNumber(value: number, locale?: string): string {
  const size = Math.abs(value);
  if (value !== 0 && (size < 1e-6 || size >= 1e15)) return String(value);
  return new Intl.NumberFormat(locale, FULL).format(value);
}

export function formatSigned(value: number, locale?: string): string {
  return value > 0 ? `+${formatNumber(value, locale)}` : formatNumber(value, locale);
}

export const ADVERSE_WORDS: Record<AdverseStatus, string> = {
  adverse: "Worse by more than the threshold",
  not_adverse: "Within the threshold",
  not_applicable: "Not comparable",
};

export const KIND_WORDS: Record<MetricChange["kind"], string> = {
  increased: "went up",
  decreased: "went down",
  added: "only in the new snapshot",
  removed: "only in the baseline",
};

export function definitionWords(d: MetricDefinition | undefined, locale?: string): string {
  if (!d) return "No direction or threshold declared";
  const better = d.direction === "higher_is_better" ? "Higher is better" : "Lower is better";
  return `${better}; threshold ${formatNumber(d.regression_threshold, locale)}`;
}
