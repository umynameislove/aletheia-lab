import type { ReactNode } from "react";
import { counterfactualStatus, projectView, COUNTERFACTUAL_MARKER } from "../data/view";
import { AFTER_DELETE, useView } from "../service";
import { useRoute, useStore } from "../store";
import { Absent } from "./primitives";

/* Gated areas stay in the map so the boundary is visible — never so it can be filled with a preview.
   Each says only what the service reports, nothing inferred. */

function GatedPage({ title, heading, code, children, after }: { title: string; heading: string; code?: string; children?: ReactNode; after?: ReactNode }) {
  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner page-inner--narrow">
        <header className="page-head"><h1>{title}</h1></header>
        <div style={{ marginTop: "var(--s6)", maxWidth: 640 }}>
          <Absent title={heading} code={code}>{children}</Absent>
          {after}
        </div>
      </div>
    </div>
  );
}

/* A closed area still offers the way back to the work, so the page is never a dead end, and a
   keyboard user who tabs away can always tab back into it (it scrolls at small sizes). */
function BackToWork() {
  const { hrefFor } = useRoute();
  const { s } = useStore();
  // a deleted project has no investigation to return to
  return (
    <div className="actions">
      {s.deletedProject
        ? <a className="btn btn--sm" href={hrefFor("start")}>Go to Start</a>
        : <a className="btn btn--sm" href={hrefFor("investigation")}>Back to the investigation</a>}
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
    ? <GatedPage title="Counterfactual" heading="Not available" code={COUNTERFACTUAL_MARKER} after={<BackToWork />}>No valid sibling pair was reported for this result.</GatedPage>
    : <GatedPage title="Counterfactual" heading="Not evaluated" code="no marker" after={<BackToWork />}>The service reported no availability for this result.</GatedPage>;
}

export function Prospective() {
  return <GatedPage title="Prospective quality" heading="Out of scope for this build" after={<BackToWork />} />;
}

/* A deleted project refuses its own deep links — but says so, instead of substituting anything. */
export function ProjectDeleted() {
  const { hrefFor } = useRoute();
  return (
    <GatedPage title="Project deleted" heading="Deleted in this session">
      {AFTER_DELETE} <a className="btn btn--sm" style={{ marginLeft: 8 }} href={hrefFor("retention")}>See the receipt</a>
    </GatedPage>
  );
}

/* An address this app has no screen for. It says so and offers the way back. */
export function NotFound() {
  const { hrefFor } = useRoute();
  return (
    <GatedPage title="Page not found" heading="This address does not match any screen">
      Nothing was opened in its place. <a className="btn btn--sm" style={{ marginLeft: 8 }} href={hrefFor("start")}>Go to Start</a>
    </GatedPage>
  );
}
