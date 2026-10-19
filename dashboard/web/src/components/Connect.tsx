import { useMemo, useState } from "react";
import { ArrowRight, Check, ChevronDown, ChevronRight, FolderOpen, Lock, Plus, RefreshCw, ScanSearch, X } from "lucide-react";
import { isProductError, port, PREVIEW_VERSION, useResultSwitch, linkIds, linked } from "../service";
import { rememberProject } from "../service/opened";
import { canonicalQuestion } from "../service/question";
import { declaredRuns, runMappings } from "../service/transitions";
import type { ConfirmResult } from "../service/port";
import { remedyFor } from "../service/errors";
import type { ConfirmMapping, IssueDTO, MappingCandidates, MetricDefinition, MetricDirection, PreviewImportResult, ProductView, UnavailableReason } from "../types";
import { useRoute, useStore } from "../store";
import type { Tone } from "../vocab";
import { Absent, Id, Mark, shortId } from "./primitives";

/* Connect a project — permission, then a scan you asked for, then a report you can act on.
   Every count, candidate and issue below is read from the preview_import fixture shared
   with the service owner. Nothing is hand-written, and nothing is guessed for the user. */

type Step = 1 | 2 | 3;

/* K04d: one row per observed metric. Direction and threshold start empty — there are no defaults. */
interface Def { name: string; direction: MetricDirection | ""; threshold: string }

interface Mapping {
  target: string; targetField: string; targetLabel: string;   // identifier_field · target_field
  metric: string; recordsPath: string; nameField: string; valueField: string; runIdField: string;
  stepField: string;                                          // "" means null — no step column
  defs: Def[];
  runs: string[]; baseline: string;
  configs: Record<string, string[]>;                          // run_id → attached config ids, none by default
}

const REASON: Record<UnavailableReason, string> = {
  sensitive_fields_withheld: "Field labels on this source were withheld, so it cannot be mapped. Typing them in is not offered — that would route around the withholding.",
  unsupported_source: "This source type is not supported for mapping.",
  unreadable_source: "This source could not be read.",
};

/* records_path is a list of segments; carried as JSON so the root array [] and a path named "" never collide */
const pathKey = (segs: string[]) => JSON.stringify(segs);
const pathLabel = (segs: string[]) => (segs.length ? segs.join(".") : "(root array)");
const pick = (list: string[], want: string) => (list.includes(want) ? want : "");
const target = (C: MappingCandidates, id: string) => C.targets.find(c => c.project_item_id === id);
const metric = (C: MappingCandidates, id: string) => C.metric_sources.find(c => c.project_item_id === id);

/* prefill only from what the fixture offers; anything it does not offer stays empty */
function metricFields(C: MappingCandidates, id: string, keep?: Partial<Mapping>) {
  const m = metric(C, id);
  return {
    recordsPath: m && m.records_path_candidates.length ? pathKey(m.records_path_candidates[0]) : "",
    nameField: m ? pick(m.field_names, keep?.nameField ?? "") || pick(m.field_names, "name") : "",
    valueField: m ? pick(m.field_names, keep?.valueField ?? "") || pick(m.field_names, "value") : "",
    runIdField: m && m.run_id_field_candidates.length ? m.run_id_field_candidates[0] : "",
    stepField: "",
    // names come only from the service's candidate list; when it offers none, the user adds them
    defs: (m?.metric_name_candidates ?? []).map(name => ({ name, direction: "" as const, threshold: "" })),
  };
}

/* Same rule as the backend: a finite number >= 0. Scientific notation is a valid finite number
   (1e3 = 1000); negatives, NaN, Infinity and overflow like 1e999 are not. Hex and blanks are not numbers here. */
const THRESHOLD = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
const validThreshold = (t: string) => THRESHOLD.test(t.trim()) && Number.isFinite(Number(t.trim()));
export const definitionsPayload = (defs: Def[]): MetricDefinition[] =>
  defs.map(d => ({ metric_name: d.name.trim(), direction: d.direction as MetricDirection, regression_threshold: Number(d.threshold.trim()) }));
function defaultMapping(C: MappingCandidates): Mapping {
  const t = C.targets.find(c => c.available);
  const m = C.metric_sources.find(c => c.available && c.format === "json" && c.records_path_candidates.length)
    ?? C.metric_sources.find(c => c.available);
  const runs = C.runs.filter(r => r.available && r.source_metric_item_ids.includes(m?.project_item_id ?? ""));
  const base = runs[1] ?? runs[0];
  return {
    target: t?.project_item_id ?? "", targetField: t ? pick(t.field_names, "run_id") : "",
    // only a column literally named "target" is offered up front; otherwise the user picks
    targetLabel: t ? pick(t.field_names, "target") : "",
    metric: m?.project_item_id ?? "", ...metricFields(C, m?.project_item_id ?? ""),
    runs: runs.map(r => r.run_id), baseline: base?.run_id ?? "",
    configs: {},
  };
}

/* records_path is always an array of segments: CSV [], JSON root array [], a picked candidate as-is,
   a typed path split on "." */
function recordsPath(C: MappingCandidates, m: Mapping): string[] {
  const ms = metric(C, m.metric);
  if (!ms || ms.format === "csv") return [];
  if (m.recordsPath.startsWith("[")) return JSON.parse(m.recordsPath) as string[];
  return m.recordsPath.trim() ? m.recordsPath.trim().split(".") : [];
}
/* The exact confirm_import mapping agreed with the service owner. IDs and format are taken
   verbatim from mapping_candidates; nothing here is invented. */
export function confirmPayload(C: MappingCandidates, m: Mapping): ConfirmMapping {
  const t = target(C, m.target), ms = metric(C, m.metric);
  return {
    target: { mapping_id: t?.mapping_id ?? "", project_item_id: m.target, target_field: m.targetLabel, identifier_field: m.targetField },
    metric_sources: [{
      mapping_id: ms?.mapping_id ?? "", project_item_id: m.metric, format: ms?.format ?? "json", records_path: recordsPath(C, m),
      metric_name_field: m.nameField, metric_value_field: m.valueField, run_id_field: m.runIdField, step_field: m.stepField || null,
    }],
    // runs are the tracked runs; baseline_run_id designates one of them as the reference run.
    // It is not "the other side" of a comparison: metric changes are per run across snapshots.
    runs: runMappings(m),
    baseline_run_id: m.baseline,
    metric_definitions: definitionsPayload(m.defs),
  };
}

/** runs the chosen metric source has observations for, as the preview reports them */
const observedRuns = (C: MappingCandidates, m: Mapping) =>
  C.runs.filter(r => r.available && r.source_metric_item_ids.includes(m.metric)).map(r => r.run_id);

/* the checks the confirm_import blockers enforce, mirrored so Confirm stays off until they pass */
/* [label, passes, code, warnOnly]. warnOnly checks never block Confirm — the service decides. */
function checks(C: MappingCandidates, m: Mapping): [string, boolean, string, boolean?][] {
  const t = target(C, m.target), ms = metric(C, m.metric);
  // Kiên, 2026-10-09: source_metric_item_ids is exact per source file. That is precise enough to block
  // for CSV or a JSON source with one records path and one run field; with several records paths it
  // is not tied to the chosen path, so the run checks only warn. A refusal on confirm is always
  // mapping_invalid — unknown_run / run_without_metrics are internal P3 codes, never public.
  const precise = !!ms && (ms.format === "csv" || (ms.records_path_candidates.length <= 1 && ms.run_id_field_candidates.length <= 1));
  return [
    ["Target resolves to a collected item", !!t?.available, "mapping_invalid"],
    ["Target field and identifier field exist on it", !!t && t.field_names.includes(m.targetLabel) && t.field_names.includes(m.targetField), "mapping_invalid"],
    ["Metric source resolves to a collected item", !!ms?.available, "mapping_invalid"],
    ["Metric name and value fields exist on it", !!ms && ms.field_names.includes(m.nameField) && ms.field_names.includes(m.valueField), "mapping_invalid"],
    ["Run identity is present", declaredRuns(m).length > 0 && declaredRuns(m).every(id => C.runs.some(r => r.available && r.run_id === id)), "mapping_invalid"],
    ["Reference run is selected", !!m.baseline && declaredRuns(m).includes(m.baseline), "mapping_invalid"],
    // Kiên, 2026-10-08: every run with observations in the source must be declared, and a declared
    // run without observations is refused; baseline_run_id names one of the declared runs
    // Only checked when the preview names the runs it saw in this source; otherwise the service decides on confirm.
    ["Every run with metrics in this source is declared", observedRuns(C, m).every(r => declaredRuns(m).includes(r)), "mapping_invalid", !precise],
    ["Every declared run has metrics in this source", declaredRuns(m).length > 0 && (!observedRuns(C, m).length || declaredRuns(m).every(r => observedRuns(C, m).includes(r))), "mapping_invalid", !precise],
    // K04d — the client mirrors the mapping_invalid rules; an exact match against the observed
    // names is checked by the service on confirm when the names were typed in
    ["Every metric has a direction and a threshold", m.defs.length > 0 && m.defs.every(d => d.name.trim() && d.direction && d.threshold.trim()), "mapping_invalid"],
    ["Thresholds are finite and ≥ 0", m.defs.length > 0 && m.defs.every(d => validThreshold(d.threshold)), "mapping_invalid"],
    ["Metric names are unique", new Set(m.defs.map(d => d.name.trim())).size === m.defs.length, "mapping_invalid"],
  ];
}

const SEV_TONE: Record<IssueDTO["severity"], Tone> = { blocker: "no", error: "no", warning: "partial", info: "" };
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Live keeps the confirmed or refreshed IDs (URL + local), so a reload can continue this project. */
export function openProject(r: { project_id: string; snapshot_id: string }) {
  if (!linked) return;
  rememberProject({ project_id: r.project_id, snapshot_id: r.snapshot_id });
  linkIds({ project: r.project_id, snapshot: r.snapshot_id, result: null });
}

export function Connect() {
  const { go } = useRoute();
  const { toast } = useStore();
  const { showResult } = useResultSwitch();
  const [step, setStep] = useState<Step>(1);
  // the folder the service may read: typed in Live, fixed in the demo. Held in this screen's state and
  // sent only in the preview_import request body — never in the URL, storage or a log.
  const [root, setRoot] = useState("");
  const granted = !!root.trim();
  const [preview, setPreview] = useState<PreviewImportResult | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [m, setM] = useState<Mapping | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [busy, setBusy] = useState(false);
  const scanned = !!preview, confirmed = !!result;

  const refuse = (e: unknown) => setRefusal(isProductError(e)
    ? { code: e.code, message: e.safe_message }
    : { code: "unavailable", message: "The service did not answer." });
  const reset = () => { setStep(1); setRoot(""); setPreview(null); setResult(null); setM(null); setRefusal(null); };
  const reached = (s: Step) => s === 1 || (s === 2 && scanned) || (s === 3 && confirmed);

  const scan = async () => {
    setBusy(true); setRefusal(null);
    try {
      // the demo port opens nothing; the live port hands the typed root to the service, which reads it read-only
      const p = await port.preview_import(root.trim());
      setPreview(p); setM(defaultMapping(p.mapping_candidates)); setStep(2);
      toast("Scan complete", `${p.included_count} included, ${p.redacted_count} redacted, ${p.excluded_count} excluded, ${p.withheld_count} withheld`);
    } catch (e) { refuse(e); } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!preview || !m) return;
    setBusy(true); setRefusal(null);
    try {
      const confirmed = await port.confirm_import(preview.preview_id, confirmPayload(preview.mapping_candidates, m));
      openProject(confirmed);
      setResult(confirmed);
      setStep(3);
    } catch (e) { refuse(e); } finally { setBusy(false); }
  };

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1>Connect a project</h1>
        </header>

        <ol className="stepper" aria-label="Import steps">
          {([[1, "Permission"], [2, "Preview & mapping"], [3, "Validate & ask"]] as const).map(([n, label]) => {
            const done = step > n, cur = step === n;
            return (
              <li key={n}>
                <button className={`step${cur ? " cur" : ""}${done ? " done" : ""}`} disabled={!reached(n)} aria-current={cur ? "step" : undefined} onClick={() => setStep(n)}>
                  <span className="step-n">{done ? <Check size={13} strokeWidth={3} /> : n}</span>
                  <span className="step-t"><b>{label}</b></span>
                </button>
              </li>
            );
          })}
        </ol>

        {step === 1 && <Permission root={root} setRoot={setRoot} granted={granted} busy={busy} refusal={refusal}
          onScan={scan} />}
        {step === 2 && preview && m && <Preview preview={preview} m={m} setM={setM} busy={busy} refusal={refusal}
          onConfirm={confirm}
          onCancel={() => { reset(); toast("Import cancelled · nothing created"); }} />}
        {step === 3 && preview && result && <Validation preview={preview} result={result} onAnalyzed={view => {
          reset(); showResult(view); go("investigation");
          toast("Analysis recorded", shortId(view.result.id));
        }} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ step 1 */
const DEMO_ROOT = "synthetic-ml-project";
const folderName = (root: string) => root.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() || root.trim();

function Permission({ root, setRoot, granted, busy, refusal, onScan }: {
  root: string; setRoot: (r: string) => void; granted: boolean; busy: boolean; refusal: Refusal; onScan: () => void;
}) {
  const demo = port.kind === "demo";
  return (
    <>
      <div className="pair">
        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Grant one folder</span></div>
          {!demo && (
            <label className="fld path-fld">
              <span className="fld-label">Folder path</span>
              <input className="input mono-in" value={root} spellCheck={false} autoComplete="off" placeholder="/path/to/your-ml-project"
                onChange={e => setRoot(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && granted && !busy) onScan(); }} />
            </label>
          )}
          {granted ? (
            <div className="notice notice--ok" style={{ marginTop: 0 }}>
              <Mark tone="ok" />
              <div><b>Access granted</b><code className="mono-s">{folderName(root)}</code>{demo && " (demo)"}</div>
            </div>
          ) : (
            <div className="grant-empty">
              <FolderOpen size={22} aria-hidden="true" />
              <div><b>No folder chosen</b></div>
            </div>
          )}
          <div className="actions">
            {demo && <button className={`btn${granted ? "" : " btn--primary"}`} onClick={() => setRoot(DEMO_ROOT)}><FolderOpen size={14} aria-hidden="true" />{granted ? "Choose a different folder" : "Choose folder"}</button>}
            <button className={`btn${granted ? " btn--primary" : ""}`} onClick={onScan} disabled={!granted || busy}><ScanSearch size={14} aria-hidden="true" />{busy ? "Scanning…" : "Scan project"}</button>
          </div>
          <RefusalNote refusal={refusal} />
          <p className="local-note"><Lock size={12} aria-hidden="true" />The path stays on this screen.</p>
        </section>

        <aside className="panel">
          <div className="sec-head"><span className="eyebrow">What this grant allows</span></div>
          {([["Read", true], ["Execute", false], ["External send", false], ["Write to source", false]] as const).map(([k, on]) => (
            <div className="np-row" key={k}>
              {on ? <Mark tone="ok" /> : <Mark hollow />}
              <span style={{ color: "var(--ink)", fontWeight: 500 }}>{k}</span>
              <span className="v">{on ? "allowed" : "blocked"}</span>
            </div>
          ))}
        </aside>
      </div>

    </>
  );
}

/* ------------------------------------------------------------------ step 2 */
function Preview({ preview, m, setM, busy, refusal, onConfirm, onCancel }: {
  preview: PreviewImportResult; m: Mapping; setM: (m: Mapping) => void; busy: boolean; refusal: Refusal; onConfirm: () => void; onCancel: () => void;
}) {
  const C = preview.mapping_candidates;
  const t = target(C, m.target), ms = metric(C, m.metric);
  const res = useMemo(() => checks(C, m), [C, m]);
  const failing = res.filter(r => !r[1] && !r[3]);
  const warned = res.filter(r => !r[1] && r[3]);
  const set = (patch: Partial<Mapping>) => setM({ ...m, ...patch });
  const runOpts = C.runs.map(r => ({ value: r.run_id, label: r.run_id, available: r.available, reason: r.unavailable_reason }));
  const cfgLabel = (id: string) => C.configs.find(c => c.project_item_id === id)?.label ?? id;
  const ws = preview.warnings;
  const sevSummary = (["error", "warning", "info"] as const).map(s => [s, ws.filter(w => w.severity === s).length] as const)
    .filter(([, n]) => n).map(([s, n]) => plural(n, s)).join(" · ");

  return (
    <div className="cols">
      <div className="stack-lg">
        <section>
          <div className="sec-head"><span className="eyebrow">Scope of this import</span><code className="aside mono-s">preview_import {PREVIEW_VERSION}</code></div>
          <div className="denoms" role="list">
            {([["Included", preview.included_count, "usable as they are", "ok"], ["Redacted", preview.redacted_count, "kept, content masked", "partial"],
              ["Excluded", preview.excluded_count, "left out of the bundle", null], ["Withheld", preview.withheld_count, "counted, content not kept", null]] as const).map(([k, v, sub, tone]) => (
              <div className="den" role="listitem" key={k} title={sub}>
                <span className="den-label tile-label">{tone ? <Mark tone={tone} /> : <Mark hollow />}{k}</span>
                <span className="den-val">{v}</span>
              </div>
            ))}
          </div>
          <p className="foot-note">
            {preview.outbound_categories.length
              ? <>Outbound: <code className="mono-s">{preview.outbound_categories.join(", ")}</code></>
              : <>Outbound: none <code className="mono-s">outbound_categories = []</code></>}
            {" "}· counted separately, not additive.
          </p>
        </section>

        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Mapping</span><code className="aside mono-s">project-mapping-configuration/v1</code></div>

          <h3 className="form-h">Target</h3>
          <div className="form-grid">
            <Picker label="Target dataset" code="target.project_item_id" value={m.target}
              options={C.targets.map(c => ({ value: c.project_item_id, label: c.label, available: c.available, reason: c.unavailable_reason }))}
              onChange={v => { const tc = target(C, v); set({ target: v, targetField: tc ? pick(tc.field_names, m.targetField) || pick(tc.field_names, "run_id") : "", targetLabel: tc ? pick(tc.field_names, m.targetLabel) : "" }); }} />
            <Picker label="Target field" code="target.target_field" value={m.targetLabel}
              options={(t?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ targetLabel: v })} placeholder="choose a field" />
            <Picker label="Row identifier field" code="target.identifier_field" value={m.targetField}
              options={(t?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ targetField: v })} />
          </div>

          <h3 className="form-h">Metric source</h3>
          <div className="form-grid">
            <Picker label="Metric source" code="metric_sources[0].project_item_id" value={m.metric}
              options={C.metric_sources.map(c => ({ value: c.project_item_id, label: `${c.label} · ${c.format}`, available: c.available, reason: c.unavailable_reason }))}
              onChange={v => set({ metric: v, ...metricFields(C, v, m) })} />
            {ms?.format === "csv"
              ? <div className="fld"><span className="fld-label">Where the records are</span><div className="locked"><Mark hollow />not needed for csv</div><code className="fld-code">metric_sources[0].records_path</code></div>
              : <Picker label="Where the records are" code="metric_sources[0].records_path" value={m.recordsPath}
                  options={(ms?.records_path_candidates ?? []).map(p => ({ value: pathKey(p), label: pathLabel(p) }))} onChange={v => set({ recordsPath: v })} />}
            <Picker label="Metric name field" code="metric_sources[0].metric_name_field" value={m.nameField}
              options={(ms?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ nameField: v })} placeholder="choose a field" />
            <Picker label="Metric value field" code="metric_sources[0].metric_value_field" value={m.valueField}
              options={(ms?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ valueField: v })} placeholder="choose a field" />
            <Picker label="Run id field" code="metric_sources[0].run_id_field" value={m.runIdField}
              options={(ms?.run_id_field_candidates ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ runIdField: v })} />
            <Picker label="Step field (optional)" code="metric_sources[0].step_field" value={m.stepField}
              options={[{ value: "", label: "none — sends null" }, ...(ms?.field_names ?? []).map(f => ({ value: f, label: f }))]} onChange={v => set({ stepField: v })} />
          </div>
          {ms && <p className="foot-note"><code className="mono-s">{ms.label}</code> is <code className="mono-s">{ms.format}</code>, read from the source profile — not guessed from its field names.</p>}

          <MetricDefinitions C={C} m={m} set={set} />

          <h3 className="form-h">Runs</h3>
          <fieldset className="config-pick" aria-label="Tracked runs">
            <legend className="fld-label">Tracked runs <code className="fld-code">runs[].run_id</code></legend>
            {C.runs.map(r => {
              const on = declaredRuns(m).includes(r.run_id);
              return <label className="ack" key={r.run_id}>
                <input type="checkbox" checked={on} disabled={!r.available} onChange={() => set({
                  runs: on ? declaredRuns(m).filter(id => id !== r.run_id) : [...declaredRuns(m), r.run_id],
                  baseline: on && m.baseline === r.run_id ? "" : m.baseline,
                })} />
                <span><code className="mono-s">{r.run_id}</code>{!r.available && " — unavailable"}</span>
              </label>;
            })}
          </fieldset>
          <div className="form-grid">
            <Picker label="Reference run" code="baseline_run_id" value={m.baseline} options={runOpts} onChange={v => set({ baseline: v })} />
          </div>
          <p className="fld-note"><Mark hollow />Metric changes are calculated per run across snapshots, not between two runs.</p>
          <div className="config-pick" role="group" aria-label="Configs attached to each run">
            <span className="fld-label">Configs attached <code className="fld-code">runs[].config_item_ids</code></span>
            {declaredRuns(m).map(run_id => {
              const offered = C.runs.find(r => r.run_id === run_id)?.available_config_item_ids ?? [];
              return offered.length ? offered.map(id => {
                const on = (m.configs[run_id] ?? []).includes(id);
                return (
                  <label className="ack" key={run_id + id}>
                    <input type="checkbox" checked={on} onChange={() => set({ configs: { ...m.configs, [run_id]: on ? (m.configs[run_id] ?? []).filter(x => x !== id) : [...(m.configs[run_id] ?? []), id] } })} />
                    <span>Attach <code className="mono-s">{cfgLabel(id)}</code> to <code className="mono-s">{run_id}</code></span>
                  </label>
                );
              }) : <p className="fld-note" key={run_id}><Mark hollow /><code className="mono-s">{run_id}</code> has no config to attach.</p>;
            })}
            <p className="fld-note">Offered by the service for you to choose — nothing is attached unless you tick it.</p>
          </div>
          <details className="meta-toggle">
            <summary><ChevronRight size={13} aria-hidden="true" />What confirm_import receives</summary>
            <pre className="payload">{JSON.stringify(confirmPayload(C, m), null, 2)}</pre>
          </details>
        </section>
      </div>

      <aside className="side">
        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Checks</span><span className="aside">{res.filter(r => r[1]).length} of {res.length} pass</span></div>
          <ul className="checklist">
            {res.map(([label, ok, code, warn]) => (
              <li key={label}>{ok ? <Mark tone="ok" /> : warn ? <Mark tone="partial" /> : <Mark hollow />}<span>{label}</span><code>{ok ? "pass" : warn ? "checked on confirm" : code}</code></li>
            ))}
          </ul>
        </section>

        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Confirm</span></div>
          {ws.length > 0 && (
            <div className="notice notice--warn" style={{ marginTop: 0 }}>
              <Mark tone="partial" />
              <div><b>{sevSummary} · non-blocking</b></div>
            </div>
          )}
          {preview.blockers.length > 0 && (
            <div className="notice notice--stop">
              <Mark tone="no" />
              <div><b>{plural(preview.blockers.length, "import blocker")} from the scan</b>{preview.blockers.map(b => b.code).join(", ")}</div>
            </div>
          )}
          <div className={`notice${failing.length ? " notice--stop" : ""}`}>
            {failing.length ? <Mark hollow /> : <Mark tone="ok" />}
            <div><b>{failing.length ? plural(failing.length, "check") + " failing" : warned.length ? "No blocking check" : "All checks pass"}</b>{warned.length > 0 && !failing.length && <span className="dim">{plural(warned.length, "run check")} left to the service on confirm.</span>}</div>
          </div>
          <div className="actions">
            <button className="btn btn--primary" disabled={failing.length > 0 || preview.blockers.length > 0 || busy} onClick={onConfirm}>{busy ? "Confirming…" : "Confirm import"}<ArrowRight size={14} aria-hidden="true" /></button>
            <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
          </div>
          <RefusalNote refusal={refusal} />
        </section>
      </aside>
    </div>
  );
}

interface Opt { value: string; label: string; available?: boolean; reason?: UnavailableReason | null }

/* Three honest states: pick from candidates · locked because withheld (no typing) · type it in because unresolved */
function Picker({ label, code, value, options, onChange, placeholder }: {
  label: string; code: string; value: string; options: Opt[]; onChange: (v: string) => void; placeholder?: string;
}) {
  const id = "f-" + code.replace(/[^a-z0-9]+/gi, "-");
  const usable = options.filter(o => o.available !== false);
  const blocked = options.filter(o => o.available === false);

  if (!usable.length && blocked.length) {
    return (
      <div className="fld">
        <span className="fld-label">{label}</span>
        <div className="locked"><Lock size={13} aria-hidden="true" />{blocked[0].label}</div>
        <code className="fld-code">{code}</code>
        <p className="fld-note">{blocked[0].reason ? REASON[blocked[0].reason] : "Not available."}</p>
      </div>
    );
  }
  if (!usable.length) {
    return (
      <div className="fld">
        <label className="fld-label" htmlFor={id}>{label}</label>
        <input id={id} className="input mono-in" value={value} onChange={e => onChange(e.target.value)} placeholder="type it in" />
        <code className="fld-code">{code}</code>
        <p className="fld-note"><Mark hollow />Not resolved by the service. Type it.</p>
      </div>
    );
  }
  return (
    <div className="fld">
      <label className="fld-label" htmlFor={id}>{label}</label>
      <span className="select-wrap">
        <select id={id} className="select" value={value} onChange={e => onChange(e.target.value)}>
          {!usable.some(o => o.value === value) && <option value={value} disabled>{placeholder ?? "choose one"}</option>}
          {options.map(o => <option key={o.value} value={o.value} disabled={o.available === false}>{o.label}{o.available === false ? " — unavailable" : ""}</option>)}
        </select>
        <ChevronDown size={14} aria-hidden="true" />
      </span>
      <code className="fld-code">{code}</code>
      {blocked.length > 0 && <p className="fld-note"><Lock size={12} aria-hidden="true" />{blocked.length} unavailable: {blocked[0].reason ? REASON[blocked[0].reason] : "unavailable"}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ step 3 */
function Validation({ preview, result, onAnalyzed }: { preview: PreviewImportResult; result: ConfirmResult; onAnalyzed: (v: ProductView) => void }) {
  // blockers holds severity "blocker" only; warnings may hold info, warning or error — each row reads its own severity
  const issues = [...preview.blockers, ...preview.warnings];
  const n = (s: IssueDTO["severity"]) => issues.filter(i => i.severity === s).length;
  // the snapshot to ask about: the confirmed one, or the one a refresh returned. The service keeps
  // evidence only for a snapshot whose refresh recorded a metric change, so asking may need a refresh first.
  const [snap, setSnap] = useState<Pick<ConfirmResult, "project_id" | "snapshot_id" | "status">>(result);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const refresh = async () => {
    setBusy(true); setRefusal(null);
    try { const r = await port.refresh(result.project_id); openProject(r); setSnap(r); }
    catch (e) { setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }); }
    finally { setBusy(false); }
  };
  return (
    <>
      <div className="denoms" role="list" aria-label="Issues by severity">
        {(["blocker", "error", "warning", "info"] as const).map(s => (
          // zero is a real count, not an absent one — it never borrows the null styling
          <div key={s} role="listitem" className="den">
            <span className="den-label tile-label">{n(s) ? <Mark tone={SEV_TONE[s]} /> : <Mark hollow />}{s}</span>
            <span className="den-val" style={n(s) === 0 ? { color: "var(--ink-3)" } : undefined}>{n(s)}</span>
          </div>
        ))}
      </div>

      {preview.blockers.length ? (
        <div className="notice notice--stop"><Mark tone="no" /><div><b>A snapshot cannot be created</b>{plural(preview.blockers.length, "blocker")} standing.</div></div>
      ) : (
        <div className="notice notice--ok">
          <Mark tone="ok" />
          <div><b>Snapshot {snap === result ? "recorded" : "refreshed"} · {snap.status}{port.kind === "demo" && " (demo)"}</b>
            <code className="mono-s"><Id v={snap.project_id} /></code> · <code className="mono-s"><Id v={snap.snapshot_id} /></code></div>
          <button className="btn btn--sm push-right" onClick={() => void refresh()} disabled={busy} title="refresh(project_id): re-scan the granted source">
            <RefreshCw size={13} aria-hidden="true" />{busy ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      )}
      <RefusalNote refusal={refusal} />
      {!preview.blockers.length && <FirstQuestion key={snap.snapshot_id} result={snap} onAnalyzed={onAnalyzed} />}

      <section className="panel" style={{ marginTop: "var(--s5)", padding: 0 }}>
        <div className="sec-head" style={{ padding: "var(--s4) var(--s5) 0" }}><span className="eyebrow">Issues</span><span className="aside">{issues.length}</span></div>
        {issues.length ? (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Code</th><th>Severity</th><th>Stage</th><th>Message</th><th>Where</th><th className="num">Count</th></tr></thead>
              <tbody>
                {issues.map((i, k) => (
                  <tr key={k}>
                    <td><code className="mono-s">{i.code}</code></td>
                    <td><span className="sev">{SEV_TONE[i.severity] ? <Mark tone={SEV_TONE[i.severity]} /> : <Mark hollow />}{i.severity}</span></td>
                    <td className="dim">{i.stage}</td>
                    <td className="msg">{i.message}</td>
                    <td>{i.relative_path === null ? <span className="dim">—</span> : <code className="mono-s">{i.relative_path}</code>}</td>
                    <td className="num">{i.occurrences}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ padding: "var(--s5)" }}><Absent title="No issues" /></div>}
      </section>
    </>
  );
}

/* ------------------------------------------------------- metric definitions */
const DIRECTIONS: { value: MetricDirection; label: string }[] = [
  { value: "higher_is_better", label: "Higher is better" },
  { value: "lower_is_better", label: "Lower is better" },
];

function MetricDefinitions({ C, m, set }: { C: MappingCandidates; m: Mapping; set: (patch: Partial<Mapping>) => void }) {
  const ms = metric(C, m.metric);
  const offered = ms?.metric_name_candidates ?? [];
  const manual = !!ms && offered.length === 0;
  const update = (i: number, patch: Partial<Def>) => set({ defs: m.defs.map((d, k) => (k === i ? { ...d, ...patch } : d)) });
  const names = m.defs.map(d => d.name.trim());

  return <>
    <h3 className="form-h">Metric definitions <code className="fld-code">metric_definitions</code></h3>

    {!ms ? (
      <Absent title="Choose a metric source first" />
    ) : <>
      {manual && (
        <p className="fld-note" style={{ marginBottom: "var(--s3)" }}>
          <Mark hollow />
          <span>No names offered. Type each exactly as in the source.</span>
        </p>
      )}

      {m.defs.length > 0 && (
        <div className="defs" role="table" aria-label="Metric definitions">
          <div className="def-row def-head" role="row">
            <span role="columnheader"><span className="fld-label">Metric</span><code className="fld-code">metric_name</code></span>
            <span role="columnheader"><span className="fld-label">Which way is better</span><code className="fld-code">direction</code></span>
            <span role="columnheader"><span className="fld-label">Regression threshold</span><code className="fld-code">regression_threshold</code></span>
            <span aria-hidden="true" />
          </div>
          {m.defs.map((d, i) => {
            const dup = d.name.trim() !== "" && names.indexOf(d.name.trim()) !== i;
            const badT = d.threshold.trim() !== "" && !validThreshold(d.threshold);
            return (
              <div className="def-row" role="row" key={i}>
                <span role="cell">
                  {manual
                    ? <input className="input mono-in" aria-label={`Metric name ${i + 1}`} value={d.name} placeholder="exact metric name" onChange={e => update(i, { name: e.target.value })} />
                    : <code className="def-name">{d.name}</code>}
                  {dup && <span className="fld-note"><Mark tone="partial" />Duplicate name</span>}
                </span>
                <span role="cell" className="select-wrap">
                  <select className="select" aria-label={`Direction for ${d.name || `metric ${i + 1}`}`} value={d.direction}
                    onChange={e => update(i, { direction: e.target.value as MetricDirection })}>
                    <option value="" disabled>choose</option>
                    {DIRECTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <ChevronDown size={14} aria-hidden="true" />
                </span>
                <span role="cell">
                  <input className="input mono-in" inputMode="decimal" aria-label={`Regression threshold for ${d.name || `metric ${i + 1}`}`}
                    value={d.threshold} placeholder="e.g. 0.05" onChange={e => update(i, { threshold: e.target.value })} />
                  {badT && <span className="fld-note"><Mark tone="partial" />A finite number ≥ 0, like 0.05</span>}
                </span>
                <span role="cell">
                  {manual && <button className="icon-btn" aria-label={`Remove metric ${d.name || i + 1}`} onClick={() => set({ defs: m.defs.filter((_, k) => k !== i) })}><X size={14} /></button>}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {manual && (
        <div className="actions" style={{ marginTop: "var(--s3)" }}>
          <button className="btn btn--sm" onClick={() => set({ defs: [...m.defs, { name: "", direction: "", threshold: "" }] })}><Plus size={13} aria-hidden="true" />Add metric</button>
        </div>
      )}

      <p className="foot-note">Threshold: absolute change in metric units, inclusive.</p>

    </>}
  </>;
}

/* The step after import: ask the first question. analyze_mock returns the new project's own view,
   so the screens never fall back to the packaged demo. */
/** `bare` drops the panel frame, for use inside another panel. */
export function FirstQuestion({ result, onAnalyzed, bare = false }: { result: Pick<ConfirmResult, "project_id" | "snapshot_id">; onAnalyzed: (v: ProductView) => void; bare?: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const q = canonicalQuestion(text);
  const ask = async () => {
    if (!q) return;
    setBusy(true); setRefusal(null);
    try { onAnalyzed(await port.analyze_mock(result.project_id, result.snapshot_id, q)); }
    catch (e) { setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }); }
    finally { setBusy(false); }
  };
  const form = (
    <>
      <div className="ask-row">
        <input className="input" aria-label="First question" placeholder="e.g. What explains the validation decrease?" value={text} disabled={busy}
          onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && q && !busy) { e.preventDefault(); void ask(); } }} />
        <button className="btn btn--primary" disabled={!q || busy} onClick={() => void ask()}>{busy ? "Asking…" : "Ask"}<ArrowRight size={14} aria-hidden="true" /></button>
      </div>
      <RefusalNote refusal={refusal} />
    </>
  );
  if (bare) return <div className="ask-bare">{form}</div>;
  return (
    <section className="panel" style={{ marginTop: "var(--s5)" }}>
      <div className="sec-head"><span className="eyebrow">Ask the first question</span><code className="aside mono-s">analyze_mock</code></div>
      {form}
    </section>
  );
}

export type Refusal = { code: string; message: string } | null;

/* code + what to do + the service's safe message; an unknown code still shows the safe message */
export function RefusalNote({ refusal }: { refusal: Refusal }) {
  if (!refusal) return null;
  const r = remedyFor(refusal.code);
  return (
    <div className="notice notice--stop" role="alert">
      <Mark tone="no" />
      <div><b>Refused · <code className="mono-s">{refusal.code}</code></b>{r.advice && <>{r.advice} </>}<span className="dim">{refusal.message}</span></div>
    </div>
  );
}
