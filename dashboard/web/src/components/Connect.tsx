import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, ChevronDown, ChevronRight, FolderOpen, Lock, Plus, RefreshCw, ScanSearch, X } from "lucide-react";
import { capabilities, isProductError, port, PREVIEW_VERSION, scoped, useResultSwitch, linkIds, linked } from "../service";
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
  // which run is the reference is the person's call: the order of the source is no evidence of it
  return {
    target: t?.project_item_id ?? "", targetField: t ? pick(t.field_names, "run_id") : "",
    // only a column literally named "target" is offered up front; otherwise the user picks
    targetLabel: t ? pick(t.field_names, "target") : "",
    metric: m?.project_item_id ?? "", ...metricFields(C, m?.project_item_id ?? ""),
    runs: runs.map(r => r.run_id), baseline: "",
    configs: {},
  };
}

/* After a rescan: every choice the new scan still offers is kept, anything it no longer offers goes
   back to empty, so nothing is carried over onto a source version it does not match. */
export function carryMapping(C: MappingCandidates, old: Mapping, was: MappingCandidates): { mapping: Mapping; dropped: boolean } {
  const fresh = defaultMapping(C);
  // an item's ID changes with its content, so a changed file is found again by its name in the project
  const same = <T extends { project_item_id: string; label: string; available: boolean }>(now: T[], before: T[], id: string) => {
    const label = before.find(c => c.project_item_id === id)?.label;
    return now.find(c => c.available && c.project_item_id === id) ?? now.find(c => c.available && c.label === label);
  };
  const t = same(C.targets, was.targets, old.target);
  const ms = same(C.metric_sources, was.metric_sources, old.metric);
  if (!t || !ms) return { mapping: fresh, dropped: true };
  const runIds = C.runs.filter(r => r.available).map(r => r.run_id);
  const defs = old.defs.filter(d => ms.metric_name_candidates.length === 0 || ms.metric_name_candidates.includes(d.name));
  const added = ms.metric_name_candidates.filter(name => !defs.some(d => d.name === name)).map(name => ({ name, direction: "" as const, threshold: "" }));
  const mapping: Mapping = {
    ...old,
    target: t.project_item_id,
    metric: ms.project_item_id,
    targetField: pick(t.field_names, old.targetField),
    targetLabel: pick(t.field_names, old.targetLabel),
    nameField: pick(ms.field_names, old.nameField),
    valueField: pick(ms.field_names, old.valueField),
    runIdField: pick(ms.run_id_field_candidates, old.runIdField),
    stepField: pick(ms.field_names, old.stepField),
    defs: [...defs, ...added],
    runs: old.runs.filter(r => runIds.includes(r)),
    baseline: pick(runIds, old.baseline),
  };
  const dropped = defs.length !== old.defs.length || mapping.runs.length !== old.runs.length || added.length > 0
    || [["targetField", "targetLabel", "nameField", "valueField", "runIdField"] as const].flat().some(k => mapping[k] !== old[k]);
  return { mapping, dropped };
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

/* Which field to send someone to when a check fails, by the check's own label. */
const CHECK_FIELD: Record<string, string> = {
  "Target resolves to a collected item": "f-target-project-item-id",
  "Target field and identifier field exist on it": "f-target-target-field",
  "Metric source resolves to a collected item": "f-metric-sources-0-project-item-id",
  "Metric name and value fields exist on it": "f-metric-sources-0-metric-name-field",
  "Run identity is present": "tracked-runs",
  "Reference run is selected": "f-baseline-run-id",
  "Every run with metrics in this source is declared": "tracked-runs",
  "Every declared run has metrics in this source": "tracked-runs",
  "Every metric has a direction and a threshold": "metric-definitions",
  "Thresholds are finite and ≥ 0": "metric-definitions",
  "Metric names are unique": "metric-definitions",
};
/* What each check means, said to the person fixing it. */
const CHECK_HELP: Record<string, string> = {
  "Target field and identifier field exist on it": "Choose the label column and the row identifier column.",
  "Metric name and value fields exist on it": "Choose the columns holding the metric's name and its value.",
  "Run identity is present": "Tick at least one run to track.",
  "Reference run is selected": "Choose which run is the reference. It is not picked for you.",
  "Every run with metrics in this source is declared": "A run with metrics in this source is not ticked.",
  "Every declared run has metrics in this source": "A ticked run has no metrics in this source.",
  "Every metric has a direction and a threshold": "Say which way is better and how much change counts, for every metric.",
  "Thresholds are finite and ≥ 0": "A threshold is a number of 0 or more, like 0.05.",
  "Metric names are unique": "The same metric is listed twice.",
};
const helpFor = (label: string) => CHECK_HELP[label] ?? label;

/* The folder and mapping survive leaving this screen and coming back. They live in memory for this
   tab only: never in the URL or storage. A hosted build clears this when the account changes. */
let draft: { root: string; preview: PreviewImportResult | null; mapping: Mapping | null } = { root: "", preview: null, mapping: null };

const STALE_PREVIEW = new Set(["preview_stale", "preview_expired", "preview_already_confirmed"]);

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
  // the folder the service may read: typed in Live, fixed in the demo. Held in memory and sent only
  // in the preview_import request body: never in the URL, storage or a log.
  const [root, setRoot] = useState(draft.root);
  const granted = !!root.trim();
  const [preview, setPreview] = useState<PreviewImportResult | null>(draft.preview);
  const [step, setStep] = useState<Step>(draft.preview ? 2 : 1);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [m, setM] = useState<Mapping | null>(draft.mapping);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [busy, setBusy] = useState(false);
  const [rescanned, setRescanned] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const scanned = !!preview, confirmed = !!result;
  const headingRef = useRef<HTMLHeadingElement>(null);

  // once confirmed there is nothing left to resume: that preview cannot be confirmed twice
  useEffect(() => { draft = result ? { root: "", preview: null, mapping: null } : { root, preview, mapping: m }; }, [root, preview, m, result]);
  // a step change is a page change for a keyboard or screen reader user
  const firstStep = useRef(true);
  useEffect(() => { if (firstStep.current) { firstStep.current = false; return; } headingRef.current?.focus(); }, [step]);

  const refuse = (e: unknown) => setRefusal(isProductError(e)
    ? { code: e.code, message: e.safe_message }
    : { code: "unavailable", message: "The service did not answer." });
  const reset = () => { setStep(1); setRoot(""); setPreview(null); setResult(null); setM(null); setRefusal(null); setRescanned(false); setShowErrors(false); };
  const reached = (s: Step) => s === 1 || (s === 2 && scanned) || (s === 3 && confirmed);

  const scan = () => {
    setBusy(true); setRefusal(null);
    const keep = m;
    const was = preview?.mapping_candidates;
    // the demo port opens nothing; the live port hands the typed root to the service, which reads it read-only
    return scoped("scan", async () => await port.preview_import(root.trim()), {
      done: p => {
        const carried = keep && was ? carryMapping(p.mapping_candidates, keep, was) : null;
        setPreview(p); setM(carried?.mapping ?? defaultMapping(p.mapping_candidates)); setStep(2);
        setRescanned(!!keep);
        toast("Scan complete", `${p.included_count} included, ${p.redacted_count} redacted, ${p.excluded_count} excluded, ${p.withheld_count} withheld`);
      },
      fail: refuse,
      always: () => setBusy(false),
    });
  };
  const confirm = () => {
    if (!preview || !m) return;
    setBusy(true); setRefusal(null);
    return scoped("confirm", async () => await port.confirm_import(preview.preview_id, confirmPayload(preview.mapping_candidates, m)), {
      done: confirmed => {
        openProject(confirmed);
        setResult(confirmed);
        setStep(3);
      },
      fail: refuse,
      always: () => setBusy(false),
    });
  };

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1 ref={headingRef} tabIndex={-1}>Connect a project</h1>
          <p className="lede">{step === 1 ? "Choose one folder. Nothing is stored until you confirm." : step === 2 ? "Check what would be read, and say what the metrics mean." : "The snapshot is stored. Ask your first question about it."}</p>
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
        {step === 2 && preview && m && <Preview preview={preview} m={m} setM={setM} busy={busy} refusal={refusal} rescanned={rescanned} showErrors={showErrors} setShowErrors={setShowErrors}
          onConfirm={confirm} onRescan={scan} canRescan={granted}
          onCancel={() => { reset(); toast("Import cancelled", "Nothing was created."); }} />}
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
  root: string; setRoot: (r: string) => void; granted: boolean; busy: boolean; refusal: Refusal; onScan: () => Promise<void>;
}) {
  const demo = !capabilities.supported_source_types.includes("local_folder");
  return (
    <>
      <div className="pair">
        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Choose one folder</span></div>
          {!demo && (
            <label className="fld path-fld">
              <span className="fld-label">Folder on this computer</span>
              <input className="input mono-in" value={root} spellCheck={false} autoComplete="off" placeholder="/path/to/your-ml-project"
                onChange={e => setRoot(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && granted && !busy) void onScan(); }} />
              <span className="fld-note">The folder holding your dataset, metrics and config files, as a full path.</span>
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
              <div><b>No folder chosen</b><p>Scanning only reads the folder. You see the result before anything is stored.</p></div>
            </div>
          )}
          <div className="actions">
            {demo && <button className={`btn${granted ? "" : " btn--primary"}`} onClick={() => setRoot(DEMO_ROOT)}><FolderOpen size={14} aria-hidden="true" />{granted ? "Choose a different folder" : "Choose folder"}</button>}
            <button className={`btn${granted ? " btn--primary" : ""}`} onClick={() => void onScan()} disabled={!granted || busy} aria-busy={busy}><ScanSearch size={14} aria-hidden="true" />{busy ? "Scanning…" : "Scan project"}</button>
          </div>
          <RefusalNote refusal={refusal} />
          <p className="local-note"><Lock size={13} aria-hidden="true" />The path stays in this tab. It is not put in the address bar or saved.</p>
        </section>

        <aside className="panel">
          <div className="sec-head"><span className="eyebrow">What Aletheia may do with it</span></div>
          {([["Read the files", true], ["Run any code in it", false], ["Send it anywhere", false], ["Change your files", false]] as const).map(([k, on]) => (
            <div className="np-row" key={k}>
              {on ? <Mark tone="ok" /> : <Mark hollow />}
              <span style={{ color: "var(--ink)", fontWeight: 500 }}>{k}</span>
              <span className="v">{on ? "Yes" : "No"}</span>
            </div>
          ))}
        </aside>
      </div>

    </>
  );
}

/* ------------------------------------------------------------------ step 2 */
function Preview({ preview, m, setM, busy, refusal, rescanned, showErrors, setShowErrors, canRescan, onConfirm, onRescan, onCancel }: {
  preview: PreviewImportResult; m: Mapping; setM: (m: Mapping) => void; busy: boolean; refusal: Refusal; rescanned: boolean;
  showErrors: boolean; setShowErrors: (on: boolean) => void; canRescan: boolean;
  onConfirm: () => Promise<void> | undefined; onRescan: () => Promise<void>; onCancel: () => void;
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
  const blocked = preview.blockers.length > 0;
  // Confirm stays pressable: pressing it with something missing says what, and goes there
  const submit = () => {
    if (failing.length > 0 || blocked) {
      setShowErrors(true);
      // after the errors have rendered: the first field marked invalid, else the group that failed, else the summary
      window.setTimeout(() => {
        const summary = document.getElementById("confirm-errors");
        const invalid = document.querySelector<HTMLElement>('main [aria-invalid="true"]');
        const group = failing.length ? document.getElementById(CHECK_FIELD[failing[0][0]] ?? "") : null;
        (invalid ?? group?.querySelector<HTMLElement>("input, select, button") ?? summary)?.focus();
      }, 0);
      return;
    }
    setShowErrors(false);
    void onConfirm();
  };
  const errorFor = (...labels: string[]) => {
    if (!showErrors) return undefined;
    const hit = failing.find(f => labels.includes(f[0]));
    return hit ? helpFor(hit[0]) : undefined;
  };
  const sevSummary = (["error", "warning", "info"] as const).map(s => [s, ws.filter(w => w.severity === s).length] as const)
    .filter(([, n]) => n).map(([s, n]) => plural(n, s)).join(" · ");

  return (
    <div className="cols">
      <div className="stack-lg">
        <section>
          <div className="sec-head"><span className="eyebrow">What would be read</span><code className="aside mono-s fld-code">preview_import {PREVIEW_VERSION}</code></div>
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
              ? <>Nothing is sent anywhere now. If sending to a model is ever enabled, these kinds of data could be included: <code className="mono-s">{preview.outbound_categories.join(", ")}</code>.</>
              : <>Nothing is sent anywhere, and no data here could be included if sending is ever enabled.</>}
            {" "}These counts overlap, so they do not add up to a total.
          </p>
        </section>

        <section className="panel">
          <div className="sec-head"><span className="eyebrow">Tell Aletheia what the files mean</span><code className="aside mono-s fld-code">project-mapping-configuration/v1</code></div>
          {rescanned && <div className="notice notice--warn" role="status"><Mark tone="partial" /><div><b>Scanned again</b>Choices the new scan still offers were kept, and the rest are empty. Check them against the source as it is now.</div></div>}

          <h3 className="form-h">Target</h3>
          <div className="form-grid">
            <Picker label="Target dataset" code="target.project_item_id" value={m.target}
              options={C.targets.map(c => ({ value: c.project_item_id, label: c.label, available: c.available, reason: c.unavailable_reason }))}
              onChange={v => { const tc = target(C, v); set({ target: v, targetField: tc ? pick(tc.field_names, m.targetField) || pick(tc.field_names, "run_id") : "", targetLabel: tc ? pick(tc.field_names, m.targetLabel) : "" }); }} />
            <Picker label="Target field" code="target.target_field" value={m.targetLabel} hint="The column holding the label, for example target or churned."
              error={errorFor("Target field and identifier field exist on it") && !t?.field_names.includes(m.targetLabel) ? "Choose the label column." : undefined}
              options={(t?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ targetLabel: v })} placeholder="choose a field" />
            <Picker label="Row identifier field" code="target.identifier_field" value={m.targetField} hint="The column that names each row, for example id."
              error={errorFor("Target field and identifier field exist on it") && !t?.field_names.includes(m.targetField) ? "Choose the identifier column." : undefined}
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
            <Picker label="Metric name field" code="metric_sources[0].metric_name_field" value={m.nameField} hint="The column saying which metric a row is, for example name."
              error={errorFor("Metric name and value fields exist on it") && !ms?.field_names.includes(m.nameField) ? "Choose the metric name column." : undefined}
              options={(ms?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ nameField: v })} placeholder="choose a field" />
            <Picker label="Metric value field" code="metric_sources[0].metric_value_field" value={m.valueField} hint="The column holding the number, for example value."
              error={errorFor("Metric name and value fields exist on it") && !ms?.field_names.includes(m.valueField) ? "Choose the metric value column." : undefined}
              options={(ms?.field_names ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ valueField: v })} placeholder="choose a field" />
            <Picker label="Run id field" code="metric_sources[0].run_id_field" value={m.runIdField}
              options={(ms?.run_id_field_candidates ?? []).map(f => ({ value: f, label: f }))} onChange={v => set({ runIdField: v })} />
            <Picker label="Step field (optional)" code="metric_sources[0].step_field" value={m.stepField}
              options={[{ value: "", label: "none — sends null" }, ...(ms?.field_names ?? []).map(f => ({ value: f, label: f }))]} onChange={v => set({ stepField: v })} />
          </div>
          {ms && <p className="foot-note"><code className="mono-s">{ms.label}</code> is <code className="mono-s">{ms.format}</code>, read from the source profile — not guessed from its field names.</p>}

          <MetricDefinitions C={C} m={m} set={set} error={errorFor("Every metric has a direction and a threshold", "Thresholds are finite and ≥ 0", "Metric names are unique")} />

          <h3 className="form-h">Runs</h3>
          <fieldset className="config-pick" id="tracked-runs" aria-label="Tracked runs">
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
            <Picker label="Reference run" code="baseline_run_id" value={m.baseline} options={runOpts} onChange={v => set({ baseline: v })} placeholder="choose a run"
              hint="The run you treat as the reference, usually your current production model."
              error={errorFor("Reference run is selected")} />
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
              <li key={label}>{ok ? <Mark tone="ok" /> : warn ? <Mark tone="partial" /> : <Mark hollow />}<span>{label}</span><code className="fld-code">{ok ? "pass" : warn ? "checked on confirm" : code}</code></li>
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
            <button className="btn btn--primary" disabled={busy} aria-busy={busy} onClick={submit}>{busy ? "Confirming…" : "Confirm import"}<ArrowRight size={14} aria-hidden="true" /></button>
            <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
          </div>
          {showErrors && (failing.length > 0 || blocked) && (
            <div className="fld-errors" id="confirm-errors" role="alert" tabIndex={-1}>
              <b>{blocked ? "This scan cannot be imported as it is." : "Before confirming:"}</b>
              <ul>{failing.map(f => <li key={f[0]}><button className="link-btn" onClick={() => document.getElementById(CHECK_FIELD[f[0]] ?? "")?.scrollIntoView({ block: "center" })}>{helpFor(f[0])}</button></li>)}</ul>
            </div>
          )}
          <RefusalNote refusal={refusal} />
          {refusal && STALE_PREVIEW.has(refusal.code) && canRescan && (
            <div className="actions"><button className="btn" disabled={busy} onClick={() => void onRescan()}><RefreshCw size={14} aria-hidden="true" />Scan again and keep my choices</button></div>
          )}
          {refusal?.code === "project_already_imported" && (
            <div className="actions"><a className="btn" href="#/start">Go to Start</a></div>
          )}
        </section>
      </aside>
    </div>
  );
}

interface Opt { value: string; label: string; available?: boolean; reason?: UnavailableReason | null }

/* Three honest states: pick from candidates · locked because withheld (no typing) · type it in because unresolved */
function Picker({ label, code, value, options, onChange, placeholder, hint, error }: {
  label: string; code: string; value: string; options: Opt[]; onChange: (v: string) => void; placeholder?: string; hint?: string; error?: string;
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
        <input id={id} className="input mono-in" value={value} onChange={e => onChange(e.target.value)} placeholder="type it in" aria-invalid={!!error} aria-describedby={error ? id + "-err" : undefined} />
        <code className="fld-code">{code}</code>
        <p className="fld-note"><Mark hollow />The scan could not tell. Type it exactly as in your file.</p>
        {error && <p className="fld-error" id={id + "-err"}><Mark tone="no" />{error}</p>}
      </div>
    );
  }
  return (
    <div className="fld">
      <label className="fld-label" htmlFor={id}>{label}</label>
      <span className="select-wrap">
        <select id={id} className="select" value={value} onChange={e => onChange(e.target.value)} aria-invalid={!!error} aria-describedby={error ? id + "-err" : hint ? id + "-hint" : undefined}>
          {!usable.some(o => o.value === value) && <option value={value} disabled>{placeholder ?? "choose one"}</option>}
          {options.map(o => <option key={o.value} value={o.value} disabled={o.available === false}>{o.label}{o.available === false ? " — unavailable" : ""}</option>)}
        </select>
        <ChevronDown size={14} aria-hidden="true" />
      </span>
      <code className="fld-code">{code}</code>
      {hint && !error && <p className="fld-note" id={id + "-hint"}>{hint}</p>}
      {error && <p className="fld-error" id={id + "-err"}><Mark tone="no" />{error}</p>}
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
  const refresh = () => {
    setBusy(true); setRefusal(null);
    return scoped("refresh", () => port.refresh(result.project_id), {
      done: r => { openProject(r); setSnap(r); },
      fail: e => setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }),
      always: () => setBusy(false),
    });
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
          <div><b>Snapshot {snap === result ? "stored" : snap.status === "unchanged" ? "unchanged, the source is the same" : "refreshed"}{port.kind === "demo" && " (demo)"}</b>
            <code className="mono-s"><Id v={snap.snapshot_id} /></code></div>
          <button className="btn btn--sm push-right" onClick={() => void refresh()} disabled={busy} title="Read the granted folder again">
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

function MetricDefinitions({ C, m, set, error }: { C: MappingCandidates; m: Mapping; set: (patch: Partial<Mapping>) => void; error?: string }) {
  const ms = metric(C, m.metric);
  const offered = ms?.metric_name_candidates ?? [];
  const manual = !!ms && offered.length === 0;
  const update = (i: number, patch: Partial<Def>) => set({ defs: m.defs.map((d, k) => (k === i ? { ...d, ...patch } : d)) });
  const names = m.defs.map(d => d.name.trim());

  return <>
    <h3 className="form-h">What each metric means <code className="fld-code">metric_definitions</code></h3>
    <p className="fld-note">Aletheia does not guess these. A threshold of 0.05 means a change of more than 0.05, in the worse direction, counts as a regression.</p>
    {error && <p className="fld-error" role="alert"><Mark tone="no" />{error}</p>}

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
        <div className="defs" id="metric-definitions" role="table" aria-label="Metric definitions">
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
  const ask = () => {
    if (!q) return;
    setBusy(true); setRefusal(null);
    return scoped("analyze", () => port.analyze_mock(result.project_id, result.snapshot_id, q), {
      done: onAnalyzed,
      fail: e => setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }),
      late: view => ({ project_id: view.project.id, snapshot_id: view.snapshot.id, result_id: view.result.id, question: q }),
      always: () => setBusy(false),
    });
  };
  const form = (
    <>
      <div className="ask-row">
        <input className="input" aria-label="First question" placeholder="For example: what changed in validation accuracy?" value={text} disabled={busy}
          onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && q && !busy) { e.preventDefault(); void ask(); } }} />
        <button className="btn btn--primary" disabled={!q || busy} aria-busy={busy} onClick={() => void ask()}>{busy ? "Asking…" : "Ask"}<ArrowRight size={14} aria-hidden="true" /></button>
      </div>
      <RefusalNote refusal={refusal} />
    </>
  );
  if (bare) return <div className="ask-bare">{form}</div>;
  return (
    <section className="panel" style={{ marginTop: "var(--s5)" }}>
      <div className="sec-head"><span className="eyebrow">Ask the first question</span></div>
      <p className="fld-note" style={{ marginBottom: "var(--s3)" }}>The answer is scripted and local: no model runs, and nothing leaves this computer.</p>
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
