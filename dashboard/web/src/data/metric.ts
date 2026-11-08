/* What a person types for a metric definition, read the way the service reads it. */

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

/* The service's identifier rule for a metric name it can match (candidates.py, _IDENTIFIER). A name
   outside it is never offered by a scan and is refused on confirm, whatever else is right. */
const METRIC_NAME = /^[A-Za-z0-9][A-Za-z0-9._:@+-]*$/;

/** Why the service would refuse this typed metric name, or null when it would accept it. */
export function metricNameProblem(name: string): string | null {
  const n = name.trim();
  if (!n) return null;
  if (!METRIC_NAME.test(n)) return "Use only letters A–Z, digits and . _ : @ + -, starting with a letter or digit. Names with other characters, such as accents, cannot be imported yet.";
  return n.length > 128 ? "At most 128 characters." : null;
}
