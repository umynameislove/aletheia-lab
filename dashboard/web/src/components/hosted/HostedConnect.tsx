import { useEffect, useRef, useState } from "react";
import type { ProductView } from "../../types";
import type { createHostedClient, HostedPreview, HostedSource, HostedSubmission, HostedUploadResume, OutputExpectation } from "../../service/hosted-client";
import { HostedRequestDiscarded } from "../../service/hosted-client";
import type { HostedCapabilities, HostedSession, JobReceipt } from "../../service/hosted";
import { ProductError } from "../../service/port";
import { declaredRuns } from "../../service/transitions";
import { checks, confirmPayload, defaultMapping, MetricDefinitions, Picker, type Mapping } from "../Connect";
import { RefusalNote } from "../Ask";
import { JobControls } from "./JobControls";

interface Props {
  client: ReturnType<typeof createHostedClient>;
  session: HostedSession;
  workspaceId: string;
  caps: HostedCapabilities;
  isCurrent: () => boolean;
  onView: (view: ProductView, jobId: string) => void;
  onBack: () => void;
  onJob?: (job: string | null) => void;
}

type Attempt =
  | ({ operation: "upload"; file: File; key: string } & HostedUploadResume)
  | { operation: "job"; submission: HostedSubmission; expected: OutputExpectation; key: string; receipt: JobReceipt | null };

/** A new account, session or membership gets an empty private form before it renders. */
export function HostedConnect(props: Props) {
  if (!props.isCurrent()) return null;
  return <ConnectForm key={`${props.session.user_id}/${props.session.context_id}/${props.workspaceId}/${props.caps.membership_revision}`} {...props} />;
}

function ConnectForm(props: Props) {
  const latest = useRef(props);
  latest.current = props;
  const alive = useRef(true), running = useRef(false), stop = useRef<AbortController | null>(null);
  const pending = useRef<Attempt | null>(null), input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<HostedSource | null>(null);
  const [preview, setPreview] = useState<HostedPreview | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<ProductError | null>(null);
  const [receipt, setReceipt] = useState<JobReceipt | null>(null);
  const [jobState, setJobState] = useState<string | null>(null);
  const [retryOf, setRetryOf] = useState<string | null>(null);
  const [showChecks, setShowChecks] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const { workspaceId, caps } = props;
  const current = () => alive.current && latest.current.isCurrent();
  const canImport = caps.workspace_id === workspaceId && caps.allowed_actions.includes("import") && caps.supported_source_types.includes("zip_bundle");
  const canAnalyze = caps.allowed_actions.includes("analyze");
  const questionLimit = Math.min(2000, caps.question_chars);
  const locked = !!busy || pending.current !== null;
  const C = preview?.mapping_candidates;
  const target = C?.targets.find(item => item.project_item_id === mapping?.target);
  const metric = C?.metric_sources.find(item => item.project_item_id === mapping?.metric);
  const results = C && mapping ? checks(C, mapping) : [];
  const failing = results.filter(([, ok, , warning]) => !ok && !warning);
  const terminal = jobState !== null && ["failed", "cancelled", "expired"].includes(jobState);
  const canResume = (pending.current?.operation === "job" && pending.current.receipt !== null) || caps.features["jobs.idempotency"] === true
    || (pending.current?.operation === "upload" && caps.features["sources.resumable"] === true);
  const uploadEnded = pending.current?.operation === "upload" && ["upload_expired", "upload_cancelled"].includes(failure?.code ?? "");

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      stop.current?.abort();
      latest.current.onJob?.(null);
      pending.current = null;
      if (input.current) input.current.value = "";
    };
  }, []);

  useEffect(() => { if (!busy) heading.current?.focus(); }, [preview, snapshot]);

  const set = (patch: Partial<Mapping>) => {
    if (!current() || running.current || pending.current) return;
    setMapping(value => value ? { ...value, ...patch } : value);
  };

  const previewAttempt = (uploaded: HostedSource, granted: HostedCapabilities): Extract<Attempt, { operation: "job" }> => ({ operation: "job",
    submission: { operation: "preview", source_id: uploaded.source_id },
    expected: { operation: "preview", sourceSha256: uploaded.sha256,
      ...(granted.features["sources.preview_lease"] === true ? { leaseSourceId: uploaded.source_id } : {}) },
    key: crypto.randomUUID(), receipt: null });

  /* A retry is a new job for the same intent: its receipt replaces the old one and is watched to its end. */
  function retried(next: JobReceipt) {
    const attempt = pending.current;
    if (!current() || running.current || attempt?.operation !== "job" || !attempt.receipt) return;
    setRetryOf(attempt.receipt.job_id);
    attempt.receipt = next; setReceipt(next); latest.current.onJob?.(next.job_id);
    void continueAttempt();
  }

  async function continueAttempt() {
    if (!current() || running.current || !pending.current) return;
    running.current = true;
    setFailure(null);
    setJobState(null);
    const controller = new AbortController();
    stop.current = controller;
    const { client, caps: granted, workspaceId: workspace } = latest.current;
    try {
      let attempt = pending.current;
      if (attempt.operation === "upload") {
        setBusy("Preparing ZIP…");
        const uploaded = await client.uploadZip(workspace, granted, attempt.file, attempt.key, { resume: attempt, signal: controller.signal,
          onProgress: status => { if (current()) setBusy(`Uploading ZIP… ${status.received_bytes.toLocaleString()} of ${status.byte_size.toLocaleString()} bytes confirmed`); } });
        if (!current()) return;
        setSource(uploaded);
        attempt = previewAttempt(uploaded, granted);
        pending.current = attempt;
      }
      setBusy(`Waiting for ${attempt.submission.operation}…`);
      if (!attempt.receipt) {
        setReceipt(null); setRetryOf(null);
        const accepted = await client.submitJob(workspace, granted, attempt.submission, attempt.key, controller.signal);
        if (!current()) return;
        attempt.receipt = accepted;
        setReceipt(accepted);
        latest.current.onJob?.(accepted.job_id);
      }
      const job = await client.pollJob(workspace, attempt.receipt.job_id, granted, { signal: controller.signal, timeoutMs: 120_000 });
      if (!current()) return;
      setJobState(job.status);
      if (job.status !== "succeeded") {
        // jobs.conflict_v1: a confirm that met a changed project, or the same ZIP already saved with another mapping,
        // ends in conflict. It says so; nothing is retried or replaced.
        if (job.status === "failed" && granted.features?.["jobs.conflict_v1"] === true) {
          const history = await client.loadJobAttempts(workspace, granted, attempt.receipt.job_id, controller.signal).catch(() => null);
          if (!current()) return;
          if (history?.items.at(-1)?.failure_kind === "conflict") throw new ProductError("confirm_conflict", `The ${attempt.submission.operation} job met a project that changed after it was accepted. Nothing was replaced.`);
        }
        throw new ProductError("job_failed", `The ${attempt.submission.operation} job ended as ${job.status}. No output was used.`);
      }
      const output = await client.loadOutput(workspace, attempt.receipt.job_id, attempt.expected, controller.signal);
      if (!current()) return;
      pending.current = null;
      if (output.operation === "preview") {
        setPreview(output.result);
        setMapping(defaultMapping(output.result.mapping_candidates, granted.features["mapping.class_unit_v2"] === true));
      } else if (output.operation === "confirm") {
        setSnapshot(output.result.snapshot_id);
      } else if (output.operation === "analyze") {
        latest.current.onView(output.result, output.job_id);
      }
    } catch (error) {
      if (!current() || error instanceof HostedRequestDiscarded) return;
      if (error instanceof ProductError && ["preview_expired", "source_changed", "preview_required"].includes(error.code)
        && pending.current?.operation === "job" && pending.current.receipt === null) pending.current = null;
      setFailure(error instanceof ProductError ? error : new ProductError("unavailable", "The work could not be confirmed. Check its state before continuing."));
    } finally {
      if (current()) { running.current = false; setBusy(null); }
      if (stop.current === controller) stop.current = null;
    }
  }

  function scan() {
    if (!current() || running.current || pending.current || !canImport) return;
    if (!file || !file.name.toLowerCase().endsWith(".zip") || file.size === 0 || file.size > caps.upload_bundle_bytes) {
      setFailure(new ProductError("invalid_request", "Choose a nonempty ZIP within this workspace's upload limit."));
      input.current?.focus();
      return;
    }
    pending.current = { operation: "upload", file, key: crypto.randomUUID() };
    void continueAttempt();
  }

  function confirm() {
    if (!current() || running.current || pending.current || !C || !mapping || !source || !canImport) return;
    setShowChecks(true);
    const runs = declaredRuns(mapping);
    const configsValid = runs.every(runId => (mapping.configs[runId] ?? []).every(id =>
      C.runs.find(run => run.run_id === runId && run.available)?.available_config_item_ids.includes(id)
      && C.configs.some(config => config.project_item_id === id && config.available)));
    if (failing.length || !metric?.run_id_field_candidates.includes(mapping.runIdField) || !configsValid) {
      setFailure(new ProductError("mapping_invalid", "Complete the mapping checks, run identity and available config choices before confirming."));
      heading.current?.focus();
      return;
    }
    const leased = caps.features["sources.preview_lease"] === true;
    if (leased && (!preview?.preview_id || preview.workspace_id !== workspaceId || preview.source_id !== source.source_id || !preview.expires_at)) {
      setFailure(new ProductError("response_out_of_scope", "Get a new preview for this workspace and ZIP before confirming.")); return;
    }
    if (leased && Date.parse(preview!.expires_at!) <= Date.now()) {
      setFailure(new ProductError("preview_expired", "The preview expired. Refresh it and review the mapping before confirming.")); return;
    }
    try {
      const payload = confirmPayload(C, mapping);
      pending.current = { operation: "job", submission: { operation: "confirm", source_id: source.source_id, mapping: { ...payload },
        ...(leased ? { preview_id: preview!.preview_id! } : {}) },
        expected: { operation: "confirm" }, key: crypto.randomUUID(), receipt: null };
      void continueAttempt();
    } catch {
      setFailure(new ProductError("mapping_invalid", "Check the selected records path before confirming."));
    }
  }

  function freshPreview() {
    if (!current() || running.current || !source || snapshot || !canImport || pending.current && !terminal) return;
    pending.current = previewAttempt(source, caps);
    setPreview(null); setMapping(null); setReceipt(null); setJobState(null); setRetryOf(null); setShowChecks(false);
    void continueAttempt();
  }

  function chooseZipAgain() {
    if (!current() || running.current || !uploadEnded) return;
    pending.current = null; setFile(null); setFailure(null); setReceipt(null); setJobState(null); setRetryOf(null);
    if (input.current) { input.current.value = ""; input.current.focus(); }
  }

  function analyze() {
    if (!current() || running.current || pending.current || !snapshot || !canAnalyze) return;
    const text = question.trim();
    if (!text || [...text].length > questionLimit) {
      setFailure(new ProductError("invalid_question", `Enter a question of 1 to ${questionLimit} characters.`));
      document.getElementById("hosted-first-question")?.focus();
      return;
    }
    pending.current = { operation: "job", submission: { operation: "analyze", snapshot_id: snapshot, question: text },
      expected: { operation: "analyze", snapshotId: snapshot }, key: crypto.randomUUID(), receipt: null };
    void continueAttempt();
  }

  function back() {
    if (!current()) return;
    stop.current?.abort();
    pending.current = null;
    if (input.current) input.current.value = "";
    alive.current = false;
    latest.current.onBack();
  }

  return <div className="stack-lg">
    <div className="sec-head"><span className="eyebrow">Connect a project</span><button type="button" className="btn btn--ghost" onClick={back}>Back</button></div>
    <h1 ref={heading} tabIndex={-1}>{snapshot ? "Snapshot saved. Ask your first question." : preview ? "Tell Aletheia what your files mean" : "Upload a project ZIP"}</h1>
    <p className="lede">{snapshot ? "This first answer observes the saved snapshot. It does not compare snapshots or establish a cause." : "The ZIP is sent to your workspace. Review its mapping before creating a snapshot."}</p>
    {!canImport ? <p role="status">Your current workspace access does not permit ZIP imports.</p> : <>
      <section className="panel">
        <label className="fld-label" htmlFor="hosted-project-zip">Project ZIP</label>
        <input ref={input} id="hosted-project-zip" type="file" accept=".zip,application/zip" disabled={locked || !!source}
          aria-describedby="hosted-upload-limit" onChange={event => {
            if (running.current || pending.current || source) return;
            const next = event.target.files?.[0] ?? null;
            if (next && (!next.name.toLowerCase().endsWith(".zip") || next.size === 0 || next.size > caps.upload_bundle_bytes)) {
              event.target.value = ""; setFile(null);
              setFailure(new ProductError("invalid_request", "Choose a nonempty ZIP within this workspace's upload limit."));
            } else { setFile(next); setFailure(null); }
          }} />
        <p className="fld-note" id="hosted-upload-limit">Maximum {caps.upload_bundle_bytes.toLocaleString()} bytes.{file && <> Selected: {file.size.toLocaleString()} bytes.</>}</p>
        {!preview && <button type="button" className="btn btn--primary" disabled={locked || !file} onClick={scan}>Upload and preview</button>}
        {source && <p className="fld-note">Accepted source SHA-256: <code className="mono-s">{source.sha256}</code></p>}
        {preview && <>
          <p className="fld-note">Preview state SHA-256: <code className="mono-s">{preview.source_state_sha256}</code></p>
          <p>{C?.targets.length} target candidates · {C?.metric_sources.length} metric sources · {C?.runs.length} runs</p>
          {preview.expires_at && <p className="fld-note">Preview expires at {new Date(preview.expires_at).toLocaleString()}. A refreshed preview needs a new review.</p>}
        </>}
        {source && !snapshot && (preview || terminal) && <button type="button" className="btn" disabled={!!busy || !!pending.current && !terminal} onClick={freshPreview}>Refresh preview</button>}
      </section>
      {C && mapping && !snapshot && <>
        <fieldset className="panel" disabled={locked}>
          <legend className="fld-label">Choose the data and metrics</legend>
          <div className="form-grid">
            <Picker label="Target source" code="target.project_item_id" value={mapping.target}
              options={C.targets.map(item => ({ value: item.project_item_id, label: item.label, available: item.available, reason: item.unavailable_reason }))}
              onChange={id => { const selected = C.targets.find(item => item.project_item_id === id && item.available); if (selected) {
                const fresh = defaultMapping({ ...C, targets: [selected] }, mapping.metadata);
                set({ target: id, targetField: fresh.targetField, targetLabel: fresh.targetLabel });
              } }} />
            <Picker label="Metric source" code="metric_sources[0].project_item_id" value={mapping.metric}
              options={C.metric_sources.map(item => ({ value: item.project_item_id, label: item.label, available: item.available, reason: item.unavailable_reason }))}
              onChange={id => { const selected = C.metric_sources.find(item => item.project_item_id === id && item.available); if (selected) {
                const fresh = defaultMapping({ ...C, metric_sources: [selected] }, caps.features["mapping.class_unit_v2"] === true);
                set({ ...fresh, target: mapping.target, targetField: mapping.targetField, targetLabel: mapping.targetLabel });
              } }} />
          </div>
          <fieldset className="form-grid" disabled={!target?.available || !target.field_names.length}>
            <legend className="fld-label">Target fields</legend>
            <Picker label="Target field" code="target.target_field" value={mapping.targetLabel} options={(target?.field_names ?? []).map(value => ({ value, label: value }))} onChange={value => set({ targetLabel: value })} />
            <Picker label="Row identifier" code="target.identifier_field" value={mapping.targetField} options={(target?.field_names ?? []).map(value => ({ value, label: value }))} onChange={value => set({ targetField: value })} />
          </fieldset>
          <fieldset className="form-grid" disabled={!metric?.available || !metric.field_names.length}>
            <legend className="fld-label">Metric fields</legend>
            {metric?.format === "json" && <Picker label="Where the records are" code="metric_sources[0].records_path" value={mapping.recordsPath}
              options={metric.records_path_candidates.map(path => ({ value: JSON.stringify(path), label: path.length ? path.join(".") : "(root array)" }))} onChange={value => set({ recordsPath: value })} />}
            <Picker label="Metric name field" code="metric_sources[0].metric_name_field" value={mapping.nameField} options={(metric?.field_names ?? []).map(value => ({ value, label: value }))} onChange={value => set({ nameField: value })} />
            <Picker label="Metric value field" code="metric_sources[0].metric_value_field" value={mapping.valueField} options={(metric?.field_names ?? []).map(value => ({ value, label: value }))} onChange={value => set({ valueField: value })} />
            <Picker label="Run id field" code="metric_sources[0].run_id_field" value={mapping.runIdField} options={(metric?.run_id_field_candidates ?? []).map(value => ({ value, label: value }))} onChange={value => set({ runIdField: value })} />
            <Picker label="Step field (optional)" code="metric_sources[0].step_field" value={mapping.stepField} options={[{ value: "", label: "None" }, ...(metric?.field_names ?? []).map(value => ({ value, label: value }))]} onChange={value => set({ stepField: value })} />
          </fieldset>
          <fieldset disabled={!metric?.available}><legend className="fld-label">Metric definitions</legend><MetricDefinitions C={C} m={mapping} set={set} allowMetadata={caps.features["mapping.class_unit_v2"] === true} error={showChecks && failing.some(([label]) => label.includes("metric") || label.includes("Threshold")) ? "Give every metric a name, direction and finite threshold." : undefined} /></fieldset>
          <fieldset className="config-pick" id="hosted-tracked-runs"><legend className="fld-label">Tracked runs</legend>
            {C.runs.map(run => <label className="ack" key={run.run_id}><input type="checkbox" checked={mapping.runs.includes(run.run_id)} disabled={!run.available} onChange={event => set({
              runs: event.target.checked ? [...mapping.runs, run.run_id] : mapping.runs.filter(id => id !== run.run_id),
              baseline: !event.target.checked && mapping.baseline === run.run_id ? "" : mapping.baseline,
            })} /><span>{run.run_id}{!run.available && " — unavailable"}</span></label>)}
          </fieldset>
          <Picker label="Reference run" code="baseline_run_id" value={mapping.baseline} placeholder="choose a run"
            options={C.runs.map(run => ({ value: run.run_id, label: run.run_id, available: run.available && mapping.runs.includes(run.run_id), reason: run.unavailable_reason }))} onChange={value => set({ baseline: value })} />
          <p className="fld-note">Choose the reference yourself. The first snapshot does not imply a comparison between runs.</p>
          <fieldset className="config-pick"><legend className="fld-label">Optional configs attached to each run</legend>
            {declaredRuns(mapping).map(runId => {
              const offered = C.runs.find(run => run.run_id === runId)?.available_config_item_ids ?? [];
              const configs = C.configs.filter(config => offered.includes(config.project_item_id));
              return <div key={runId}><p className="fld-note">{runId}{!configs.length && ": no configs offered"}</p>{configs.map(config => <label className="ack" key={config.project_item_id}>
                <input type="checkbox" disabled={!config.available} checked={(mapping.configs[runId] ?? []).includes(config.project_item_id)} onChange={event => set({ configs: { ...mapping.configs,
                  [runId]: event.target.checked ? [...(mapping.configs[runId] ?? []), config.project_item_id] : (mapping.configs[runId] ?? []).filter(id => id !== config.project_item_id),
                } })} /><span>{config.label}{!config.available && " — unavailable"}</span>
              </label>)}</div>;
            })}
          </fieldset>
        </fieldset>
        <section className="panel"><h2 className="form-h">Before confirming</h2>
          <ul className="checklist">{results.map(([label, ok, , warning]) => <li key={label}><span>{label}</span><span>{ok ? "Pass" : warning ? "Checked by service" : "Needed"}</span></li>)}</ul>
          <button type="button" className="btn btn--primary" disabled={locked} onClick={confirm}>Confirm mapping and save snapshot</button>
        </section>
      </>}
      {snapshot && <section className="panel"><p>Snapshot saved: <code className="mono-s">{snapshot}</code></p>
        {canAnalyze ? <><label className="fld-label" htmlFor="hosted-first-question">Your question</label>
          <textarea id="hosted-first-question" className="input" rows={3} value={question} disabled={locked} aria-describedby="hosted-question-limit" onChange={event => setQuestion(event.target.value)} />
          <p className="fld-note" id="hosted-question-limit">{[...question].length} / {questionLimit} characters</p>
          <button type="button" className="btn btn--primary" disabled={locked} onClick={analyze}>Analyze saved snapshot</button>
        </> : <p role="status">Your current access does not permit analysis.</p>}
      </section>}
    </>}
    <div role="status" aria-live="polite">{busy}{jobState && !busy && <>Job state: {jobState}.</>}</div>
    {receipt && <p className="fld-note">Accepted job: <code className="mono-s">{receipt.job_id}</code>. Its acceptance receipt is separate from its current state.</p>}
    <RefusalNote refusal={failure ? { code: failure.code, message: failure.safe_message } : null} />
    {receipt && <JobControls key={receipt.job_id} client={props.client} caps={caps} workspaceId={workspaceId} jobId={receipt.job_id}
      state={busy ? null : jobState} waiting={!!busy && !jobState} retryOf={retryOf} isCurrent={current} onRetried={retried} />}
    {uploadEnded && !busy && <button type="button" className="btn" onClick={chooseZipAgain}>Choose ZIP again</button>}
    {pending.current && !busy && !terminal && !uploadEnded && <div className="actions">
      <p className="fld-note">{pending.current.operation === "upload" ? "The upload outcome may be unknown. Retrying sends the same ZIP with the same request key." : pending.current.receipt ? "Check the saved job again; no new job will be submitted." : "The submission may have been accepted. Retrying keeps the same values and request key."}</p>
      {!canResume && <p className="fld-note">The service has not enabled safe request retries. The outcome remains unconfirmed.</p>}
      <button type="button" className="btn" disabled={!canResume} onClick={() => { if (canResume) void continueAttempt(); }}>{pending.current.operation === "upload" ? "Retry same upload" : pending.current.receipt ? "Check saved job" : "Retry same request"}</button>
    </div>}
  </div>;
}
