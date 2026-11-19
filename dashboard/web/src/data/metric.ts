/* What a person types for a metric definition, read the way the service reads it. */
import { MAPPING_LETTERS_NUMBERS, MAPPING_MARKS, METRIC_LABEL_CHARACTERS } from "./mapping-characters.ts";

/* Same rule as the backend: a finite number >= 0. Scientific notation is a valid finite number
   (1e3 = 1000); negatives, NaN, Infinity and overflow like 1e999 are not. Hex and blanks are not numbers
   here. A decimal comma is read as a point: the app shows numbers in the reader's locale (0,08 where
   that is the convention), and a decimal keypad there may have no "." key. One separator at most. */
const THRESHOLD = /^(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][+-]?\d+)?$/;

/** The threshold as the number the service receives, or null when it is not one. */
export function parseThreshold(text: string): number | null {
  const t = text.trim();
  if (!THRESHOLD.test(t)) return null;
  const n = Number(t.replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

/* Match the service's canonical Unicode identifier rule without rewriting source labels. */
const METRIC_PUNCTUATION = "._:@+-";

/** Why the service would refuse this typed metric name, or null when it would accept it. */
export function metricNameProblem(name: string): string | null {
  const n = name.trim();
  if (!n) return null;
  const characters = Array.from(n);
  if (!MAPPING_LETTERS_NUMBERS.test(characters[0]) || characters.some(character =>
    !MAPPING_LETTERS_NUMBERS.test(character) && !MAPPING_MARKS.test(character) && !METRIC_PUNCTUATION.includes(character))) {
    return "Use letters, digits and . _ : @ + -, starting with a letter or digit.";
  }
  if (n.normalize("NFC") !== n) return "Use composed Unicode (NFC) characters for this name.";
  return characters.length > 128 ? "At most 128 characters." : null;
}

/** Canonical labels keep their exact source text, without scaling or rewriting. */
export const isMetricLabel = (text: unknown, maximum: number): text is string => typeof text === "string"
  && text.length > 0 && text === text.trim() && text === text.normalize("NFC")
  && Array.from(text).length <= maximum && METRIC_LABEL_CHARACTERS.test(text);
