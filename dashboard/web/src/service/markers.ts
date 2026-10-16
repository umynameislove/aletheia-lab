/* Fixed strings the service writes into the view. Plain TypeScript (no JSON import) so the demo
   port and the contract tests can share them under Node's type stripping. */

/** K07: at most once per missing_evidence array, always last; a status, not an evidence request. */
export const COUNTERFACTUAL_MARKER = "Counterfactual comparison: not_available";
