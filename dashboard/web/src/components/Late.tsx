import type { MouseEvent } from "react";
import { ArrowRight, X } from "lucide-react";
import { dismissLate, linked, useLateWork, useRunningWork } from "../service";
import { useRoute } from "../store";
import { Mark, plainClick } from "./primitives";
import { RefusalNote } from "./Ask";

const OP_WORDS: Record<string, string> = {
  analyze: "An analysis", follow_up: "A follow-up", confirm: "An import", refresh: "A refresh", scan: "A scan", export: "An export",
};

/* Dismissing removes the row under focus: focus goes to the next row's Dismiss, else the previous one's,
   else (the list is gone) to the screen. */
function dismiss(e: MouseEvent<HTMLButtonElement>, id: number) {
  const row = e.currentTarget.closest(".action-row");
  const near = (row?.nextElementSibling ?? row?.previousElementSibling)?.querySelector<HTMLElement>("[data-dismiss]");
  (near ?? document.getElementById("main"))?.focus();
  dismissLate(id);
}

/* Work that finished after the person moved on. It never changed the screen they went to, so it
   waits here until they choose to open it. Only opaque IDs and their own question are held. */
export function LateWorkList() {
  const late = useLateWork();
  const running = useRunningWork();
  const { hrefFor } = useRoute();
  if (!late.length && !running.length) return null;
  return (
    <section className="panel late" aria-label="Your recent work">
      {running.length > 0 && <div className="sec-head"><span className="eyebrow">Still running</span></div>}
      {running.map(w => (
        <div className="np-row" key={w.id} role="status">
          <Mark hollow />
          <span><span className="row-k">{OP_WORDS[w.op] ?? w.op} is running</span>{w.label && <span className="sub-line">{w.label}</span>}</span>
        </div>
      ))}
      {late.length > 0 && <div className="sec-head"><span className="eyebrow">Finished after you moved on</span></div>}
      {late.map(w => {
        const what = `${OP_WORDS[w.op] ?? w.op} ${w.error ? "did not finish" : "finished"}`;
        return (
        <div className="np-row action-row" key={w.id}>
          {w.error ? <Mark tone="no" /> : <Mark tone="accent" />}
          <span>
            <span className="row-k">{what}</span>
            {w.result && <span className="sub-line">{w.result.question}</span>}
            {/* the code and what to do about it; the message itself is not kept. Not an alert: this list is read again on every visit */}
            {w.error && <RefusalNote refusal={{ code: w.error, message: "" }} compact alert={false} />}
          </span>
          <span className="n-act">
            {/* a link to the result's own address (Live): opened here it leaves this list; Cmd/Ctrl-click
                opens it in a new tab and keeps it here. The demo never puts a result in the address, so there
                it opens the sample's investigation and is not named after the late question. */}
            {w.result && (
              <a className="btn btn--sm"
                href={hrefFor("investigation", linked ? { project: w.result.project_id, snapshot: w.result.snapshot_id, result: w.result.result_id } : undefined)}
                onClick={e => { if (plainClick(e)) dismissLate(w.id); }}
              >Open{linked && <span className="sr">: {w.result.question}</span>}<ArrowRight size={13} aria-hidden="true" /></a>
            )}
            {/* an import or refresh has no result to open; its project was kept on arrival, and Start continues it */}
            {!w.result && !w.error && linked && (w.op === "confirm" || w.op === "refresh") && (
              <a className="btn btn--sm" href={hrefFor("start")} onClick={e => { if (plainClick(e)) dismissLate(w.id); }}>
                Continue<span className="sr">: the project from {w.op === "confirm" ? "the import" : "the refresh"}</span><ArrowRight size={13} aria-hidden="true" />
              </a>
            )}
            <button className="icon-btn" data-dismiss aria-label={`Dismiss: ${what}${w.result ? `, ${w.result.question}` : ""}`} onClick={e => dismiss(e, w.id)}><X size={14} /></button>
          </span>
        </div>
        );
      })}
    </section>
  );
}
