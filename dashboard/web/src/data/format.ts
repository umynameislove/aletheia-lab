/* Numbers for reading (Q-08.02, Q-14.05). Formatting changes separators only: the value is never
   rounded, so a displayed number cannot cross a threshold the service did not report. null is
   unknown and stays visibly unknown, never 0. Delta and adverse_status are the service's, never recomputed. */

import type { AdverseStatus, MetricDefinition, MetricChange } from "../types.ts";

const FULL = { maximumFractionDigits: 20 } as const;

/** Grouping and decimal mark by locale, every significant digit kept. Values Intl would lose keep their exact form. */
export function formatNumber(value: number, locale?: string): string {
  const size = Math.abs(value);
  if (value !== 0 && (size < 1e-6 || size >= 1e15)) return String(value);
  // -0 is a floating-point artefact of a subtraction, not a direction: it reads as 0
  return new Intl.NumberFormat(locale, FULL).format(value === 0 ? 0 : value);
}

export function formatSigned(value: number, locale?: string): string {
  return value > 0 ? `+${formatNumber(value, locale)}` : formatNumber(value, locale);
}

export const ADVERSE_WORDS: Record<AdverseStatus, string> = {
  adverse: "Worse by at least the threshold",
  not_adverse: "No regression at this threshold",
  not_applicable: "Not comparable",
};

export function definitionWords(d: MetricDefinition | undefined, locale?: string): string {
  if (!d) return "No direction or threshold declared";
  // only the two the contract names: a direction read wrongly would reverse what "worse" means
  const better = d.direction === "higher_is_better" ? "Higher is better" : d.direction === "lower_is_better" ? "Lower is better" : "Direction not recognised";
  return `${better}; threshold ${formatNumber(d.regression_threshold, locale)}${"unit" in d ? `; ${d.unit === null ? "unknown unit" : `unit ${d.unit}`}` : ""}`;
}

export function metricDimensions(change: MetricChange): string {
  const value = change.after ?? change.before;
  const label = value?.class_label, unit = value?.unit, origin = value?.unit_origin;
  return `${label === undefined ? "Class not supplied" : label === null ? "No class column selected" : `Class: ${label}`} · ${unit == null ? "Unknown unit" : `Unit: ${unit} (${origin === "source" ? "from source" : "declared"})`}`;
}

/** A file name that says what it is: readable project name, the result's short ID. ASCII only, because
    file names travel through systems that mangle anything else; nothing here is a path. */
export function reportFilename(project: string, resultId: string, ext: string): string {
  const slug = project.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[đĐ]/g, "d")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
  const hex = /([0-9a-f]{8,})$/.exec(resultId)?.[1];
  const short = hex ? hex.slice(0, 8) : resultId.replace(/[^A-Za-z0-9]+/g, "-").slice(-16);
  return `aletheia-${slug || "project"}-${short}.${ext}`;
}
