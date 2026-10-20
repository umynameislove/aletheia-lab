import type { ReactNode } from "react";
import { counterfactualStatus, projectView, COUNTERFACTUAL_MARKER } from "../data/view";
import { AFTER_DELETE, useView } from "../service";
import { useRoute, useStore } from "../store";
import { Absent } from "./primitives";

/* Gated areas stay in the map so the boundary is visible — never so it can be filled with a preview.
   Each says only what the service reports, nothing inferred. */

function GatedPage({ title, heading, code, children }: { title: string; heading: string; code?: string; children?: ReactNode }) {
  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head"><h1>{title}</h1></header>
        <div style={{ marginTop: "var(--s6)", maxWidth: 640 }}>
          <Absent title={heading} code={code}>{children}</Absent>
        </div>
      </div>
    </div>
  );
}

/* K07: availability comes only from the service's marker in missing_evidence, matched exactly.
   With no marker the UI concludes nothing. */
export function Counterfactual() {
  const VIEW = useView();
  const { s } = useStore();
  const said = counterfactualStatus(projectView(VIEW, s)) === "not_available";
  return said
    ? <GatedPage title="Counterfactual" heading="Not available" code={COUNTERFACTUAL_MARKER}>No valid sibling pair was reported for this result.</GatedPage>
    : <GatedPage title="Counterfactual" heading="Not evaluated" code="no marker">The service reported no availability for this result.</GatedPage>;
}

export function Prospective() {
  return <GatedPage title="Prospective quality" heading="Out of scope for this build" />;
}

/* A deleted project refuses its own deep links — but says so, instead of substituting anything. */
export function ProjectDeleted() {
  const { go } = useRoute();
  return (
    <GatedPage title="Project deleted" heading="Deleted in this session">
      {AFTER_DELETE} <button className="btn btn--sm" style={{ marginLeft: 8 }} onClick={() => go("retention")}>See the receipt</button>
    </GatedPage>
  );
}
