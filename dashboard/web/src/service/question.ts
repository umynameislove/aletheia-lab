/* follow_up question rules, as locked by the service owner (K08, 2026-10-08).

   The service never trims or normalises: it takes the string byte for byte and refuses anything
   that is empty, has leading/trailing whitespace, is over 2000 characters, is not Unicode NFC, or
   holds a control character (newline and tab included) — all with invalid_question. Replay identity
   is the exact tuple (result_id, selection_kind, selection_id, question).

   The UI is allowed to canonicalise what the person typed before it sends (trim + NFC — a Vietnamese
   IME on macOS can emit decomposed marks), so a correct question is never refused for its encoding.
   What it sends is then the exact string the service keys on. */

export const QUESTION_MAX = 2000;
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;

/** What the UI sends: trimmed and NFC. Nothing else is changed. */
export const canonicalQuestion = (typed: string) => typed.trim().normalize("NFC");

/** Why the service would refuse this exact string, or null if it would accept it. */
export function questionProblem(q: unknown): string | null {
  if (typeof q !== "string" || q.length === 0) return "Write a question to ask.";
  if (q !== q.trim()) return "The question starts or ends with whitespace.";
  if ([...q].length > QUESTION_MAX) return `Keep the question to ${QUESTION_MAX} characters.`;
  if (CONTROL.test(q)) return "Remove line breaks, tabs and other control characters.";
  if (q !== q.normalize("NFC")) return "The question is not in Unicode NFC.";
  return null;
}
