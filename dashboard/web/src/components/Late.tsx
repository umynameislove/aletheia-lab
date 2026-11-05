import { ArrowRight, X } from "lucide-react";
import { dismissLate, linkIds, useLateWork } from "../service";
import { useRoute } from "../store";
import { Mark } from "./primitives";

const OP_WORDS: Record<string, string> = {
  analyze: "An analysis", follow_up: "A follow-up", confirm: "An import", refresh: "A refresh", scan: "A scan",
};

/* Work that finished after the person moved on. It never changed the screen they went to, so it
   waits here until they choose to open it. Only opaque IDs and their own question are held. */
export function LateWorkList() {
  const late = useLateWork();
  const { go } = useRoute();
  if (!late.length) return null;
  return (
    <section className="panel late" aria-label="Finished after you moved on">
      <div className="sec-head"><span className="eyebrow">Finished after you moved on</span></div>
      {late.map(w => (
        <div className="np-row action-row" key={w.id}>
          {w.error ? <Mark tone="no" /> : <Mark tone="accent" />}
          <span>
            <span className="row-k">{OP_WORDS[w.op] ?? w.op} {w.error ? "did not finish" : "finished"}</span>
            {w.result && <span className="sub-line">{w.result.question}</span>}
            {w.error && <code className="sub-line mono-s">{w.error}</code>}
          </span>
          <span className="n-act">
            {w.result && (
              <button className="btn btn--sm" onClick={() => {
                const r = w.result!;
                dismissLate(w.id);
                linkIds({ project: r.project_id, snapshot: r.snapshot_id, result: r.result_id });
                go("investigation");
              }}>Open<ArrowRight size={13} aria-hidden="true" /></button>
            )}
            <button className="icon-btn" aria-label="Dismiss" onClick={() => dismissLate(w.id)}><X size={14} /></button>
          </span>
        </div>
      ))}
    </section>
  );
}
