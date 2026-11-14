/* Real Chrome against the hosted build. The local HTTP transport is controlled, using maintained
   producer outputs for the data; it is not an IdP/staging or peer-acceptance claim. */
import assert from "node:assert/strict";
import { after, afterEach, before, test } from "node:test";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { extname, resolve, sep } from "node:path";
import { launch } from "./e2e/cdp.mjs";

const root = resolve(fileURLToPath(new URL("../dist/", import.meta.url)));
const fixtures = fileURLToPath(new URL("../../../fixtures/metric-dimensions-hosted-v2/", import.meta.url));
const read = name => JSON.parse(readFileSync(resolve(fixtures, name), "utf8"));
const preview = read("preview.json"), confirm = read("confirm.json"), snapshot = read("snapshot.json"), analysis = read("analyze-output.json");
const refreshed = JSON.parse(readFileSync(fileURLToPath(new URL("../../../fixtures/metric-dimensions-v2/product-view.json", import.meta.url)), "utf8"));
const VERSION = "product-api/v1-draft";
const workspace = { schema_version: VERSION, workspace_id: "a", workspace_name: "Synthetic workspace", membership: { role: "analyst", revision: 1 } };
let signedIn, metadata, deniedJob, holdSession, calls, submissions, output, resumable, expireUploadOnce, expireConfirmOnce;
let uploadNumber, manifest, uploadedParts, leaseNumber, previewResults, holdPartOnce, releasePart;
let bindings, projectRevision, pdfOffered, large, expireCursorOnce, deletionStages, missingChunk, graphFaults, jobControls, jobScript, paged;
// K10: a result too large to open whole, read as header, claim pages and per-claim evidence.
const LARGE = "p6-result-" + "9".repeat(64), LARGE_EVIDENCE = "p3-evidence-" + "8".repeat(64);
const largeClaim = (n, text) => ({ id: "p6-claim-" + String(n).repeat(64), turn_id: "p6-turn-" + "7".repeat(64), text, claim_type: "evidence_statement",
  epistemic_state: "observed", support: "fully_supported", citation_ids: [LARGE_EVIDENCE], counterevidence_ids: [], missing_evidence: [], max_strength: "observation" });
// K10 graph: Kiên's R3 fixture 07 (33 validated GraphView pages), re-scoped to the large result above.
const GRAPH = Array.from({ length: 33 }, (_, i) => JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/k10-graph-r3/graph-page-${String(i + 1).padStart(2, "0")}.json`, import.meta.url)), "utf8")));
// The four bindings (handover 2026-11-11): a child result and a new snapshot built from the producer's own view.
const parentView = read("view.json"), parentResult = read("result-view.json");
const CHILD = "p6-result-" + "c".repeat(64), NEW_SNAPSHOT = "upload-snapshot-" + "e".repeat(64), TOMBSTONE = "p6-deletion-" + "d".repeat(64);
const childView = (() => { const view = structuredClone(parentView); view.result.id = CHILD;
  view.conversation.turns.push({ ...view.conversation.turns[0], id: "p6-turn-" + "f".repeat(64), result_id: CHILD, question: "Why this claim?" }); return view; })();
const EXPORTED = Buffer.from('{"synthetic":"export"}\n'), PDF = Buffer.from("%PDF-1.4 synthetic\n%%EOF\n");
const BINDING_FEATURES = { "projects.refresh": true, "projects.follow_up": true, "exports.download": true, "projects.logical_delete": true, "catalog.read": true };
let server, browser, page, base;
const reset = () => { signedIn = true; metadata = true; deniedJob = false; holdSession = false; calls = []; submissions = []; output = analysis;
  resumable = false; expireUploadOnce = false; expireConfirmOnce = false; uploadNumber = 0; uploadedParts = new Map(); leaseNumber = 0; previewResults = new Map(); holdPartOnce = false; releasePart = null;
  bindings = null; projectRevision = 1; pdfOffered = false; large = false; expireCursorOnce = false; deletionStages = null; missingChunk = null; graphFaults = []; jobControls = false; jobScript = new Map(); paged = true; };
const session = () => ({ schema_version: VERSION,
  actor: { user_id: "synthetic_actor", display_name: "Synthetic account", email: "test@example.invalid", email_verified: true },
  session: { context_id: "synthetic_context", expires_at: "2030-01-01T00:00:00Z" }, csrf_token: "synthetic-csrf-only",
  onboarding: "ready", last_workspace_id: null, can_create_workspace: false });
const caps = () => ({ ...workspace, deployment_mode: "hosted", analysis_backends: ["local_evidence"],
  supported_source_types: ["zip_bundle"], supported_export_formats: bindings ? ["json", "markdown", ...(pdfOffered ? ["pdf"] : [])] : [],
  allowed_actions: bindings ? ["read", "review", "export", "delete", "import", "analyze"] : ["import", "analyze"],
  features: { ...(metadata ? { "mapping.class_unit_v2": true } : {}),
    ...(resumable ? { "sources.resumable": true, "sources.preview_lease": true } : {}),
    ...(bindings === "on" ? BINDING_FEATURES : bindings === "off" ? { "catalog.read": true } : {}),
    ...(large || paged ? { "catalog.paged_read": true } : {}),
    ...(deletionStages ? { "projects.deletion_status": true } : {}),
    ...(jobControls ? { "jobs.execution_v1": true, "jobs.cancel": true, "jobs.retry": true } : {}) },
  limits: { question_chars: 2000, upload_bundle_bytes: 1_048_576, page_max: 50, job_poll_min_ms: 1000 } });
const problem = (code, status) => ({ schema_version: VERSION, type: "urn:test:denied", title: "Denied", status,
  code, detail: "Synthetic safe refusal.", request_id: "synthetic_request" });
const body = async request => { const parts = []; for await (const part of request) parts.push(part); return Buffer.concat(parts); };

before(async () => {
  assert.ok(existsSync(resolve(root, "index.html")), "run npm run build:hosted first");
  reset();
  server = createServer(async (request, response) => {
    const path = new URL(request.url, "http://localhost").pathname;
    const json = (value, status = 200) => { response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" }); response.end(JSON.stringify(value)); };
    if (!path.startsWith("/v1/")) {
      const filename = resolve(root, path === "/" ? "index.html" : "." + path);
      if (!filename.startsWith(root + sep) && filename !== resolve(root, "index.html")) { response.writeHead(404).end(); return; }
      // K16: one lazy screen's code chunk is missing (404) or its connection drops (offline)
      if (missingChunk && path.startsWith("/assets/" + missingChunk.name + "-") && path.endsWith(".js")) {
        if (missingChunk.offline) request.socket.destroy(); else response.writeHead(404).end();
        return;
      }
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" };
      try { response.writeHead(200, { "Content-Type": types[extname(filename)] ?? "application/octet-stream", "Cache-Control": "no-store" }); response.end(readFileSync(filename)); }
      catch { response.writeHead(404).end(); }
      return;
    }
    const bytes = await body(request); calls.push({ method: request.method, path, headers: request.headers, text: bytes.toString("utf8"), at: performance.now() });
    if (path === "/v1/session") {
      if (holdSession) return; // cleanup destroys the connection; a hung authority must cover the view.
      return signedIn ? json(session()) : json(problem("session_expired", 401), 401);
    }
    if (path === "/v1/auth/renew") return json(session());
    if (path === "/v1/auth/logout" || path === "/v1/auth/logout-all") { signedIn = false; response.writeHead(204).end(); return; }
    if (path === "/v1/workspaces/a/capabilities") return json(caps());
    if (path === "/v1/workspaces") return json({ schema_version: VERSION, items: [workspace], next_cursor: null });
    if (path === "/v1/workspaces/a") return json(workspace);
    const uploadStatus = state => ({ schema_version: VERSION, workspace_id: "a", upload_id: "upload_" + uploadNumber,
      state, byte_size: manifest.byte_size, sha256: manifest.sha256, expires_at: "2030-01-01T00:00:00Z",
      received_bytes: [...uploadedParts.values()].reduce((sum, bytes) => sum + bytes.length, 0), received_parts: [...uploadedParts.keys()],
      missing_parts: manifest.parts.map(part => part.part_number).filter(number => !uploadedParts.has(number)), source_id: state === "ready" ? preview.source_id : null });
    if (path === "/v1/workspaces/a/uploads") {
      manifest = JSON.parse(bytes); uploadNumber++; uploadedParts = new Map();
      const source = readFileSync(resolve(fixtures, "source.zip"));
      assert.equal(manifest.sha256, createHash("sha256").update(source).digest("hex"));
      assert.equal(manifest.byte_size, source.length);
      if (expireUploadOnce) { expireUploadOnce = false; return json(uploadStatus("expired"), 201); }
      return json(uploadStatus("active"), 201);
    }
    const part = /^\/v1\/workspaces\/a\/uploads\/upload_\d+\/parts\/(\d+)$/.exec(path);
    if (part) {
      const number = Number(part[1]);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), manifest.parts[number - 1].sha256);
      uploadedParts.set(number, bytes);
      if (holdPartOnce) { holdPartOnce = false; releasePart = () => json(uploadStatus("active")); return; }
      return json(uploadStatus("active"));
    }
    if (/\/uploads\/upload_\d+\/complete$/.test(path)) return json(uploadStatus("ready"));
    if (path === "/v1/workspaces/a/sources") return json({ schema_version: VERSION, workspace_id: "a", source_id: preview.source_id, sha256: preview.source_sha256 }, 201);
    if ((path === "/v1/workspaces/a/jobs" || /\/imports\/(preview|prev_[0-9a-f]{32}\/confirm)$/.test(path)) && request.method === "POST") {
      const body = JSON.parse(bytes);
      const value = { ...body, operation: path.endsWith("/imports/preview") ? "preview" : path.endsWith("/confirm") ? "confirm" : body.operation };
      submissions.push(value);
      if (path.endsWith("/confirm") && expireConfirmOnce) { expireConfirmOnce = false; return json(problem("preview_expired", 410), 410); }
      const job = value.operation === "analyze" ? analysis.job_id : "job_" + value.operation + "_v2" + (resumable ? "_" + submissions.length : "");
      if (value.operation === "preview") {
        leaseNumber++;
        previewResults.set(job, { ...preview, preview_id: "prev_" + leaseNumber.toString(16).padStart(32, "0") });
      }
      return json({ schema_version: VERSION, workspace_id: "a", job_id: job, request_id: "synthetic_request", status: "queued", status_url: "/v1/workspaces/a/jobs/" + job }, 202);
    }
    const B = "/v1/workspaces/a", P = parentView.project.id;
    if (path === `${B}/projects/${P}` && request.method === "GET") return json({ schema_version: VERSION, workspace_id: "a", project_id: P,
      revision: projectRevision, display_name: "Synthetic", state: "active", current_snapshot_id: parentView.snapshot.id, current_result_id: parentView.result.id });
    if (path === `${B}/projects/${P}` && request.method === "DELETE") return json({ schema_version: VERSION, workspace_id: "a", project_id: P,
      revision: Number(/^"(\d+)"$/.exec(request.headers["if-match"])[1]) + 1, tombstone_id: TOMBSTONE, status: "tombstoned", retention: "retained" });
    // K13: each read of the deletion status answers the next stage in the list, then stays on the last
    if (deletionStages && path === `${B}/projects/${P}/deletion-status`) {
      const stage = deletionStages.length > 1 ? deletionStages.shift() : deletionStages[0];
      return json({ schema_version: VERSION, workspace_id: "a", project_id: P, tombstone_id: TOMBSTONE, revision: projectRevision + 1,
        status: stage, requested_at: "2030-01-01T00:00:00Z", eligible_at: "2030-01-31T00:00:00Z", project_retention_days: 30,
        time_basis: stage === "purged" ? "restore_checkpoint" : "delete_commit", purged_at: stage === "purged" ? "2030-02-01T00:00:00Z" : null,
        records_removed: stage === "purged" ? 7 : 0, pending_objects: stage === "purged" ? 0 : 2, shared_objects_retained: 1,
        raw_source_policy: "workspace_sources_retained", backup_policy: "operator_managed_not_erased_here" });
    }
    const queued = job => json({ schema_version: VERSION, workspace_id: "a", job_id: job, request_id: "synthetic_request", status: "queued", status_url: `${B}/jobs/${job}` }, 202);
    if (path === `${B}/projects/${P}/refresh` && request.method === "POST") return queued("job_refresh");
    if (path === `${B}/results/${parentView.result.id}/follow-ups` && request.method === "POST") return queued("job_follow");
    if (path === `${B}/jobs/job_refresh/output`) return projectRevision = 2, json({ schema_version: VERSION, workspace_id: "a", job_id: "job_refresh", operation: "refresh",
      result: { project_id: P, status: "new_snapshot", previous_snapshot_id: parentView.snapshot.id, snapshot_id: NEW_SNAPSHOT, revision: 2 } });
    if (path === `${B}/jobs/job_follow/output`) return projectRevision = 3, json({ schema_version: VERSION, workspace_id: "a", job_id: "job_follow", operation: "follow_up",
      result: { project_id: P, result_id: CHILD, parent_result_id: parentView.result.id, snapshot_id: parentView.snapshot.id, revision: 3 } });
    if (large && path.startsWith(`${B}/results/${LARGE}`)) {
      const query = new URL(request.url, "http://localhost").searchParams, scope = { workspace_id: "a", project_id: P, result_id: LARGE };
      const pageOf = (items, next, total = 120000) => json({ schema_version: VERSION, ...scope, snapshot_id: parentView.snapshot.id, items,
        next_cursor: next, total, total_kind: total === null ? "unknown" : "exact" });
      if (path === `${B}/results/${LARGE}`) return json(problem("response_too_large", 413), 413);
      if (path.endsWith("/header")) return json({ schema_version: VERSION, ...scope, snapshot_id: parentView.snapshot.id,
        view_schema_version: "p6-product-view/v1", project: parentView.project, runtime: { provider: "local_evidence",
          model: "upload-snapshot-metric-observation/v2", external_call: false },
        result: { ...parentView.result, id: LARGE }, conversation_id: "conversation_large",
        turn_count: 1, claim_count: 120000, evidence_count: 240000, node_count: 22, edge_count: 30 });
      if (path.endsWith("/graph")) {
        const cursor = query.get("cursor"), fault = graphFaults.find(f => f.cursor === cursor);
        if (fault) { graphFaults.splice(graphFaults.indexOf(fault), 1); return json(problem(fault.code, fault.status), fault.status); }
        const { workspace_id, project_id, result_id, ...rest } = GRAPH[cursor ? Number(cursor.split("-").pop()) - 1 : 0];
        return json({ ...rest, ...scope });
      }
      if (path.endsWith("/claims") && query.get("search")) return pageOf([largeClaim(3, "Loss spike in run 2.")], null, null);
      if (path.endsWith("/claims") && query.get("cursor") === "page-2") {
        if (expireCursorOnce) { expireCursorOnce = false; return json(problem("cursor_expired", 410), 410); }
        return pageOf([largeClaim(2, "Accuracy series counted.")], null);
      }
      if (path.endsWith("/claims")) return pageOf([largeClaim(1, "Loss series counted."), largeClaim(4, "Learning-rate series counted.")], "page-2");
      if (path === `${B}/results/${LARGE}/evidence/${LARGE_EVIDENCE}`) return json({ schema_version: VERSION, ...scope,
        evidence_id: LARGE_EVIDENCE, claim_id: query.get("claim_id"), relation: "citation",
        evidence: { id: LARGE_EVIDENCE, kind: "metric_observation", title: "Observed metric series", text: "Every mapped row was counted.",
          reproduction_ref: null, source_sha256: "2".repeat(64), visibility: "diagnosis", redacted: false } });
    }
    // the bounded header of an ordinary result, for workspaces that offer paged reads (K15 object-scope check)
    if (paged && deniedJob && /\/results\/p6-result-[0-9a-f]{64}\/header$/.test(path)) return json(problem("record_not_found", 404), 404);
    const headed = paged && /^\/v1\/workspaces\/a\/results\/(p6-result-[0-9a-f]{64})\/header$/.exec(path);
    // a result this server hands out has its own header too: the refreshed v2 view is checked before it is shown
    if (headed && headed[1] === refreshed.result.id) return json({ schema_version: VERSION, workspace_id: "a",
      project_id: refreshed.project.id, result_id: refreshed.result.id, snapshot_id: refreshed.snapshot.id, view_schema_version: refreshed.schema_version,
      project: refreshed.project, runtime: refreshed.runtime, result: refreshed.result, conversation_id: refreshed.conversation.id,
      turn_count: refreshed.conversation.turns.length, claim_count: refreshed.claims.length, evidence_count: refreshed.evidence.length,
      node_count: refreshed.graph.nodes.length, edge_count: refreshed.graph.edges.length });
    if (headed && [parentView.result.id, CHILD].includes(headed[1])) return json({ schema_version: VERSION, workspace_id: "a",
      project_id: parentView.project.id, result_id: headed[1], snapshot_id: parentView.snapshot.id, view_schema_version: "p6-product-view/v1",
      project: parentView.project, runtime: parentView.runtime, result: { ...parentView.result, id: headed[1] }, conversation_id: parentView.conversation.id,
      turn_count: 1, claim_count: parentView.claims.length, evidence_count: parentView.evidence.length, node_count: parentView.graph.nodes.length, edge_count: parentView.graph.edges.length });
    if (path === `${B}/results/${parentView.result.id}`) return json(parentResult);
    if (path === `${B}/results/${CHILD}`) return json({ ...parentResult, result_id: CHILD, view: childView });
    const exporting = /^\/v1\/workspaces\/a\/results\/(p6-result-[0-9a-f]{64})\/exports$/.exec(path);
    if (exporting && request.method === "POST") {
      const format = JSON.parse(bytes).format, id = `ex_${format}_${exporting[1]}_${"a".repeat(32)}`, file = format === "pdf" ? PDF : EXPORTED;
      return json({ schema_version: VERSION, workspace_id: "a", project_id: P, result_id: exporting[1], export_id: id, format,
        byte_size: file.length, sha256: createHash("sha256").update(file).digest("hex"),
        download_url: `${B}/exports/${id}/download`, expires_at: "2099-01-01T00:00:00Z" }, 201);
    }
    const downloading = /^\/v1\/workspaces\/a\/exports\/ex_(json|pdf)_[^/]+\/download$/.exec(path);
    if (downloading) {
      const pdf = downloading[1] === "pdf";
      response.writeHead(200, { "Content-Type": pdf ? "application/pdf" : "application/json", "Cache-Control": "no-store" });
      response.end(pdf ? PDF : EXPORTED); return;
    }
    const match = /^\/v1\/workspaces\/a\/jobs\/([^/]+)(\/output|\/attempts|\/cancel|\/retry)?$/.exec(path);
    if (match) {
      const job = match[1], script = jobScript.get(job);
      if (deniedJob) return json(problem("record_not_found", 404), 404);
      // K09: a scripted job walks through its states; cancel and retry change only what the service reports
      if (match[2] === "/cancel") { jobScript.set(job, { states: ["cancel_requested", "cancelled"], at: 0, retryable: false });
        return json({ schema_version: VERSION, workspace_id: "a", job_id: job, status: "cancel_requested", progress: null }); }
      if (match[2] === "/retry") { const child = "job_retry_" + job.length; jobScript.set(child, { states: ["running", "succeeded"], at: 0, retryable: null });
        return json({ schema_version: VERSION, workspace_id: "a", job_id: child, status: "queued", request_id: "req_retry", status_url: `/v1/workspaces/a/jobs/${child}` }, 202); }
      if (match[2] === "/attempts") return json({ schema_version: VERSION, workspace_id: "a", job_id: job, retry_of_job_id: null,
        items: [{ epoch: 1, started_at: "2026-11-13T08:00:00Z", heartbeat_at: "2026-11-13T08:00:04Z", lease_until: "2026-11-13T08:02:00Z",
          finished_at: "2026-11-13T08:00:06Z", outcome: "failed", failure_kind: "transient" }] });
      if (!match[2] && script) { const status = script.doneAt !== undefined ? (performance.now() < script.doneAt ? "running" : "succeeded")
          : script.states[Math.min(script.at++, script.states.length - 1)];
        return json({ schema_version: VERSION, workspace_id: "a", job_id: job, status, progress: null, attempt: 1, max_attempts: 3, retryable: script.retryable }); }
      if (!match[2]) return json({ schema_version: VERSION, workspace_id: "a", job_id: job, status: "succeeded", progress: 1 });
      if (job === analysis.job_id || job.startsWith("job_retry_")) return json({ ...output, job_id: job });
      const operation = job.startsWith("job_preview_v2") ? "preview" : "confirm";
      return json({ schema_version: VERSION, workspace_id: "a", job_id: job, operation,
        result: operation === "preview" ? previewResults.get(job) : { snapshot_id: snapshot.snapshot_id } });
    }
    json(problem("record_not_found", 404), 404);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}/`;
  browser = await launch({ port: 9367 }); page = browser.page;
  await page.send("Page.bringToFront");
});
// every request a case sent, with its route pattern only (IDs and bodies left out), for the P01–P38 workload table
afterEach(t => {
  if (!process.env.E2E_EVIDENCE_DIR || !calls?.length) return;
  const shape = path => path.replace(/\/workspaces\/a(?=\/|$)/, "/workspaces/{workspace_id}").replace(/p6-result-[0-9a-f]{64}/g, "{result_id}")
    .replace(/\/jobs\/[^/]+/, "/jobs/{job_id}").replace(/\/projects\/[^/]+/, "/projects/{project_id}").replace(/\/uploads\/[^/]+/, "/uploads/{upload_id}")
    .replace(/\/parts\/\d+/, "/parts/{part_number}").replace(/\/exports\/[^/]+\/download/, "/exports/{export_id}/download")
    .replace(/\/evidence\/[^/]+/, "/evidence/{evidence_id}").replace(/\/imports\/prev_[0-9a-f]{32}/, "/imports/{preview_id}");
  const t0 = calls[0].at;
  writeFileSync(resolve(process.env.E2E_EVIDENCE_DIR, "calls-" + t.name.replace(/[^A-Za-z0-9]+/g, "-").slice(0, 80) + ".json"),
    JSON.stringify({ case: t.name, requests: calls.map(call => [Number(((call.at - t0) / 1000).toFixed(2)), call.method, shape(call.path)]) }, null, 0));
});
after(async () => { await browser?.close(); server?.closeAllConnections(); await new Promise(resolve => server?.close(resolve)); });

async function connect(expectPreview = true) {
  await page.open(base + "#/workspaces");
  await page.until(`document.body.innerText.includes("Synthetic workspace")`);
  await page.clickText("Open workspace");
  await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Connect project')`);
  await page.clickText("Connect project");
  await page.until(`document.querySelector('#hosted-project-zip')`);
  const document = await page.send("DOM.getDocument");
  const input = await page.send("DOM.querySelector", { nodeId: document.result.root.nodeId, selector: "#hosted-project-zip" });
  await page.send("DOM.setFileInputFiles", { nodeId: input.result.nodeId, files: [resolve(fixtures, "source.zip")] });
  await page.until(`[...document.querySelectorAll('button')].some(b=>b.textContent==='Upload and preview' && !b.disabled)`);
  await page.clickText("Upload and preview");
  if (expectPreview !== null) await page.until(expectPreview ? `document.querySelector('[aria-label="Direction for độ_chính_xác"]')` : `document.body.innerText.includes('upload_expired')`);
}
const setPicker = (code, value) => page.set("#f-" + code.replace(/[^a-z0-9]+/gi, "-"), value);
async function fillMapping(v2) {
  await setPicker("target.identifier_field", "id");
  await page.set('[aria-label="Direction for độ_chính_xác"]', "higher_is_better");
  await page.set('[aria-label="Regression threshold for độ_chính_xác"]', "0.08");
  await setPicker("baseline_run_id", "lần-0");
  if (v2) { await setPicker("metric_sources[0].class_field", "class"); await setPicker("metric_sources[0].unit_field", "unit"); await page.set("#def-0-unit", "%"); }
}

test("hosted build starts from the real session endpoint and leaves sign-in with the server", async () => {
  reset(); signedIn = false; await page.open(base + "#/workspaces");
  await page.until(`document.body.innerText.includes("Sign in to Aletheia")`);
  assert.equal(await page.eval(`document.querySelector('a[href="/v1/auth/start"]').textContent`), "Sign in");
  assert.equal(await page.eval(`document.querySelectorAll('input[type=password]').length`), 0);
  assert.deepEqual(calls.filter(call => call.method === "POST"), []);
});

test("Chrome completes the hosted v2 mapping with exact producer payload, opens and reloads the saved result", async () => {
  reset(); await connect();
  assert.equal(await page.eval(`document.querySelector('input[type=checkbox]').checked`), true, "capability enables the explicit v2 choice");
  assert.match(await page.eval("document.body.innerText"), /Unknown unit — no unit is inferred/);
  await fillMapping(true);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-v2-mapping.png"), true);
  await page.resize(390, 844);
  assert.ok(await page.eval("document.documentElement.scrollWidth <= innerWidth"), "metadata controls fit narrow screens");
  await page.resize(1440, 900);
  await page.clickText("Confirm mapping and save snapshot");
  await page.until("document.querySelector('#hosted-first-question')");
  assert.deepEqual(submissions.find(value => value.operation === "confirm").mapping, confirm.mapping);
  await page.eval(`(() => { const e = document.querySelector('#hosted-first-question'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(e, 'What is observed?'); e.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await page.clickText("Analyze saved snapshot");
  await page.until(`document.querySelector('.verdict')`);
  assert.match(await page.eval("location.hash"), /workspace=a&job=job_synthetic_analyze_v2/);
  await page.click('a[href^="#/project?"]');
  await page.until(`document.querySelector('#main summary')?.textContent === "Declared metric definitions"`);
  await page.click("#main summary");
  assert.match(await page.eval("document.body.innerText"), /độ_chính_xác: Higher is better; threshold 0.08; unit %/);
  assert.match(await page.eval("location.hash"), /workspace=a/);
  await page.send("Page.reload");
  await page.until(`document.querySelector('#main summary')?.textContent === "Declared metric definitions"`);
  assert.ok(calls.some(call => call.path === `/v1/workspaces/a/jobs/${analysis.job_id}/output`));
  await page.clickText("Renew session");
  await page.until(`!document.querySelector('button[disabled]') || [...document.querySelectorAll('button')].find(b=>b.textContent === 'Renew session')?.disabled === false`);
  assert.equal(calls.filter(call => call.path === "/v1/auth/renew").length, 1);
  const mutation = calls.find(call => call.path === "/v1/auth/renew");
  assert.equal(mutation.headers["x-csrf-token"], "synthetic-csrf-only");
  deniedJob = true;
  await page.until(`document.body.innerText.includes("This work is no longer available")`, 5500);
  assert.equal(await page.eval(`!!document.querySelector('.verdict, #metric-definitions, .app')`), false, "denied result leaves the DOM");
  assert.equal(await page.eval(`document.body.innerText.includes('Synthetic account')`), true, "project denial does not fabricate a logout");
});

test("the actual refreshed v2 producer view displays its exact class, source unit and unconverted operands", async () => {
  reset(); output = { ...analysis, result: refreshed };
  const query = new URLSearchParams({ workspace: "a", job: analysis.job_id, project: refreshed.project.id,
    snapshot: refreshed.snapshot.id, result: refreshed.result.id });
  await page.open(base + "#/project?" + query);
  await page.until(`document.querySelector('.tbl tbody')?.innerText.includes('dương tính')`);
  const values = await page.eval(`(() => { const row = [...document.querySelectorAll('.tbl tbody tr')].find(row => row.innerText.includes('dương tính')); return [...row.cells].map(cell => cell.innerText); })()`);
  assert.equal(await page.eval(`document.querySelector('.acct-name').textContent`), "Synthetic account");
  assert.ok(await page.eval(`document.querySelector('.acct').scrollWidth <= document.querySelector('.acct').clientWidth`));
  assert.match(values[0], /Class: dương tính.*Unit: % \(from source\)/);
  assert.match(values[2], /^0[.,]8$/); assert.match(values[3], /^0[.,]7$/); assert.match(values[4], /^-0[.,]1$/);
  assert.equal(values[5], "Worse by at least the threshold"); assert.match(values[6], /threshold 0[.,]1; unit %/);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-v2-result.png"), true);
});

test("an older hosted server never receives v2 mapping fields", async () => {
  reset(); metadata = false; await connect();
  assert.equal(await page.eval(`document.body.innerText.includes('Record classes and units explicitly')`), false);
  await fillMapping(false); await page.clickText("Confirm mapping and save snapshot");
  await page.until("document.querySelector('#hosted-first-question')");
  const mapping = submissions.find(value => value.operation === "confirm").mapping;
  assert.deepEqual(Object.keys(mapping).sort(), ["target", "metric_sources", "runs", "baseline_run_id", "metric_definitions"].sort());
  assert.equal("unit" in mapping.metric_definitions[0], false); assert.equal("class_field" in mapping.metric_sources[0], false);
  await page.clickText("Sign out all sessions");
  await page.until(`document.body.innerText.includes('Sign in to Aletheia')`);
  assert.equal(calls.filter(call => call.path === "/v1/auth/logout-all").length, 1);
  assert.equal(await page.eval(`document.body.innerText.includes('metrics.csv')`), false);
  assert.equal(await page.eval(`/synthetic-csrf-only|example.invalid/.test(JSON.stringify(localStorage) + JSON.stringify(sessionStorage))`), false);
});

test("resumable capability uses staging and an expired preview needs a fresh explicit review", async () => {
  reset(); resumable = true; expireConfirmOnce = true; await connect(); await fillMapping(true);
  assert.equal(calls.some(call => call.path.endsWith('/sources')), false);
  assert.equal(calls.filter(call => /\/uploads$/.test(call.path)).length, 1);
  assert.equal(calls.filter(call => /\/parts\/1$/.test(call.path)).length, 1);
  await page.clickText("Confirm mapping and save snapshot");
  await page.until(`document.body.innerText.includes('preview_expired')`);
  assert.equal(calls.filter(call => call.path.endsWith('/confirm')).length, 1, "no automatic fresh confirm intent");
  await page.clickText("Refresh preview");
  await page.until(`document.querySelector('[aria-label="Direction for độ_chính_xác"]')?.value === ''`);
  assert.equal(await page.eval(`document.querySelector('#def-0-unit').value`), '');
  await fillMapping(true); await page.clickText("Confirm mapping and save snapshot");
  await page.until(`document.querySelector('#hosted-first-question')`);
  const paths = calls.filter(call => call.path.endsWith('/confirm')).map(call => call.path);
  assert.equal(paths.length, 2); assert.notEqual(paths[0], paths[1], "new preview carries a new server lease");
  assert.equal(calls.filter(call => call.path.endsWith('/imports/preview')).length, 2);
  assert.equal(submissions.some(value => '_preview_id' in (value.mapping ?? {})), false);
});

test("an expired upload clears only on explicit ZIP reselection and starts with a new key", async () => {
  reset(); resumable = true; expireUploadOnce = true; await connect(false);
  await page.clickText("Choose ZIP again");
  assert.equal(await page.eval(`document.querySelector('#hosted-project-zip').files.length`), 0);
  assert.equal(calls.filter(call => call.path.endsWith('/uploads')).length, 1);
  const doc = await page.send("DOM.getDocument");
  const input = await page.send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "#hosted-project-zip" });
  await page.send("DOM.setFileInputFiles", { nodeId: input.result.nodeId, files: [resolve(fixtures, "source.zip")] });
  await page.until(`[...document.querySelectorAll('button')].some(b=>b.textContent==='Upload and preview' && !b.disabled)`);
  await page.clickText("Upload and preview");
  await page.until(`document.querySelector('[aria-label="Direction for độ_chính_xác"]')`);
  const starts = calls.filter(call => call.path.endsWith('/uploads'));
  assert.equal(starts.length, 2); assert.notEqual(starts[0].headers['idempotency-key'], starts[1].headers['idempotency-key']);
});

test("Back abandons an in-flight accepted part without completing or previewing the upload", async () => {
  reset(); resumable = true; holdPartOnce = true; await connect(null);
  const deadline = Date.now() + 5000;
  while (!releasePart && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(releasePart, "the server accepted the part while its response is held");
  await page.clickText("Back");
  await page.until(`!document.querySelector('#hosted-project-zip') && document.body.innerText.includes('Workspace access')`);
  releasePart(); await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(calls.filter(call => call.method === "POST" && call.path.endsWith("/uploads")).length, 1);
  assert.equal(calls.filter(call => call.method === "PUT").length, 1);
  assert.equal(calls.some(call => /\/complete$|\/imports\/|\/jobs$/.test(call.path)), false,
    "a route-only exit in the same session must stop the abandoned mutation sequence");
  assert.equal(await page.eval(`!!document.querySelector('#hosted-project-zip')`), false);
});

test("a stalled authority covers private forms within the hosted lease and provides recovery", async () => {
  reset(); await connect(); holdSession = true;
  await page.until(`document.querySelector('.session-cover')`, 5500);
  assert.equal(await page.eval(`document.querySelector('.session-app').hidden`), true);
  assert.equal(await page.eval(`document.querySelector('.session-app').hasAttribute('inert')`), true);
  holdSession = false;
  // No answer to an already hung request is invented; start over is offered once its deadline expires.
  await page.until(`document.body.innerText.includes('Could not confirm your access')`, 17000);
  await page.clickText("Start over");
  await page.until(`document.body.innerText.includes('Workspace access') && !document.querySelector('.session-cover')`);
  assert.equal(await page.eval(`!!document.querySelector('#hosted-project-zip')`), false);
});

const resultQuery = (result = parentView.result.id, job = analysis.job_id) => new URLSearchParams({ workspace: "a", job,
  project: parentView.project.id, snapshot: parentView.snapshot.id, result });
const mutations = () => calls.filter(call => call.method !== "GET" && !call.path.startsWith("/v1/auth/"));

test("Chrome runs the four hosted bindings: ZIP refresh, follow-up, export and logical delete, each on its server CAS", async () => {
  reset(); bindings = "on";
  await page.send("Page.setDownloadBehavior", { behavior: "deny" });
  // Refresh: a replacement ZIP is chosen, uploaded, and named by the server's source ID; the open result stays
  await page.open(base + "#/project?" + resultQuery());
  await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Refresh from ZIP' && !b.disabled)`);
  const doc = await page.send("DOM.getDocument");
  const picker = await page.send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: 'input[type=file][accept=".zip,application/zip"]' });
  await page.send("DOM.setFileInputFiles", { nodeId: picker.result.nodeId, files: [resolve(fixtures, "source.zip")] });
  await page.until(`document.body.innerText.includes('New snapshot')`, 6000);
  const refresh = calls.find(call => call.path.endsWith("/refresh"));
  assert.equal(refresh.headers["if-match"], '"1"'); assert.match(refresh.headers["idempotency-key"], /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(refresh.headers["x-csrf-token"], "synthetic-csrf-only");
  assert.deepEqual(JSON.parse(refresh.text), { schema_version: VERSION, source_id: preview.source_id });
  assert.ok(calls.findIndex(call => call.path.endsWith("/sources")) < calls.indexOf(refresh), "the ZIP is uploaded before the refresh");
  assert.match(await page.eval("document.body.innerText"), /This result stays on/);
  assert.equal(calls.some(call => call.path.endsWith("/jobs") && call.method === "POST"), false, "no analysis until someone asks");
  // Follow-up: on the open result's claim, with the revision the refresh reported; the child is read from GET results
  await page.open(base + "#/investigation?" + resultQuery() + "&claim=" + parentView.claims[0].id);
  await page.until(`document.querySelector('[aria-label="Ask a follow-up"]') && !document.querySelector('[aria-label="Ask a follow-up"]').disabled`);
  await page.set('[aria-label="Ask a follow-up"]', "Why this claim?");
  await page.click(".composer .btn--primary");
  await page.until(`location.hash.includes('result=${CHILD}') && location.hash.includes('job=job_follow')`, 6000);
  const follow = calls.find(call => call.path.endsWith("/follow-ups"));
  assert.equal(follow.headers["if-match"], '"2"');
  assert.deepEqual(JSON.parse(follow.text), { schema_version: VERSION, selection_kind: "claim", selection_id: parentView.claims[0].id, question: "Why this claim?" });
  assert.ok(calls.some(call => call.path === `/v1/workspaces/a/results/${CHILD}`));
  // Export: descriptor first, then exactly its bytes; PDF is not offered by this workspace
  await page.open(base + "#/retention?" + resultQuery(CHILD, "job_follow"));
  await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Download' && !b.disabled)`);
  assert.equal(await page.eval(`[...document.querySelectorAll('.np-row')].find(r => r.innerText.startsWith('PDF')).querySelector('button').disabled`), true);
  await page.eval(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Download').click()`);
  await page.until(`document.body.innerText.includes('Sent to your browser as')`, 6000);
  const created = calls.find(call => call.path.endsWith("/exports"));
  assert.deepEqual(JSON.parse(created.text), { format: "json" });
  assert.ok(calls.some(call => /\/exports\/ex_json_[^/]+\/download$/.test(call.path)));
  assert.equal(await page.eval(`document.body.innerText.includes('saved')`), false, "the page never says the file was saved");
  // Delete: a typed tombstone with the follow-up's revision; no counts are shown or invented
  await page.eval(`[...document.querySelectorAll('.btn--danger')].find(b => !b.disabled).click()`);
  await page.until(`document.querySelector('[role=dialog] input[type=checkbox]')`);
  assert.match(await page.eval(`document.querySelector('[role=dialog]').innerText`), /nothing is purged/);
  await page.click('[role=dialog] input[type=checkbox]');
  await page.eval(`[...document.querySelectorAll('[role=dialog] button')].find(b => b.textContent.includes('Delete project')).click()`);
  await page.until(`document.body.innerText.includes('Marked deleted (tombstoned)')`, 6000);
  const removed = calls.find(call => call.method === "DELETE");
  assert.equal(removed.headers["if-match"], '"3"'); assert.equal(removed.text, "{}");
  const receipt = await page.eval(`document.querySelector('.receipt').innerText`);
  assert.match(receipt, /Retained \(retained\)/); assert.match(receipt, new RegExp(TOMBSTONE));
  assert.doesNotMatch(receipt, /Items deleted|shared with other projects/);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-bindings-delete.png"), true);
});

test("Chrome keeps the four bindings off, with the reason beside each, when the workspace does not offer them", async () => {
  reset(); bindings = "off";
  await page.open(base + "#/project?" + resultQuery());
  await page.until(`document.body.innerText.includes('Refresh: This workspace does not offer it.')`);
  assert.equal(await page.eval(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Refresh from ZIP').disabled`), true);
  await page.open(base + "#/investigation?" + resultQuery() + "&claim=" + parentView.claims[0].id);
  await page.until(`document.querySelector('[aria-label="Ask a follow-up"]')?.disabled === true`);
  assert.match(await page.eval(`document.querySelector('[aria-label="Ask a follow-up"]').placeholder`), /does not offer it/);
  await page.open(base + "#/retention?" + resultQuery());
  await page.until(`document.body.innerText.includes('This workspace does not offer it.')`);
  for (const format of ["JSON", "Markdown", "PDF"]) {
    assert.equal(await page.eval(`[...document.querySelectorAll('.np-row')].find(r => r.innerText.startsWith('${format}')).querySelector('button').disabled`), true, format);
  }
  assert.equal(await page.eval(`[...document.querySelectorAll('.btn--danger')].every(b => b.disabled)`), true);
  assert.deepEqual(mutations(), [], "nothing was sent for a binding that is off");
});

test("Chrome offers PDF once the workspace advertises it (K12) and downloads exactly the described application/pdf bytes", async () => {
  reset(); bindings = "on"; pdfOffered = true;
  await page.send("Page.setDownloadBehavior", { behavior: "deny" });
  await page.open(base + "#/retention?" + resultQuery());
  const pdfButton = `[...document.querySelectorAll('.np-row')].find(r => r.innerText.startsWith('PDF')).querySelector('button')`;
  await page.until(`${pdfButton} && !${pdfButton}.disabled`);
  await page.eval(`${pdfButton}.click()`);
  await page.until(`document.body.innerText.includes('Sent to your browser as') && document.body.innerText.includes('.pdf')`, 6000);
  const created = calls.find(call => call.path.endsWith("/exports"));
  assert.deepEqual(JSON.parse(created.text), { format: "pdf" });
  assert.match(created.headers["idempotency-key"], /^[A-Za-z0-9_-]{16,128}$/);
  assert.ok(calls.some(call => /\/exports\/ex_pdf_[^/]+\/download$/.test(call.path)));
});

test("Chrome reads a result too large to open whole in pages (K10): exact counts, a cursor restart, search and one claim's evidence", async () => {
  reset(); bindings = "on"; large = true; expireCursorOnce = true;
  const text = () => page.eval("document.body.innerText");
  await page.open(base + "#/investigation?" + resultQuery(LARGE));
  await page.until(`document.body.innerText.includes('Showing 2 of 120,000.')`, 6000);
  assert.match(await text(), /Large result, read in pages/);
  assert.match(await text(), /Metric observation summary/, "the pinned v2 analysis is named, not just its provider");
  assert.match(await text(), /22 nodes, 30 edges/);
  assert.equal(calls.some(call => call.path.endsWith("/graph")), false, "the lineage is read only when asked for");
  // the next page's cursor has expired: the list starts again from page one, never splicing two sequences
  await page.eval(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Load more claims').click()`);
  await page.until(`document.body.innerText.includes('started again from the first page')`, 6000);
  await page.until(`document.body.innerText.includes('Showing 2 of 120,000.')`, 6000);
  await page.eval(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Load more claims').click()`);
  await page.until(`document.body.innerText.includes('Showing 3 of 120,000.')`, 6000);
  assert.equal(await page.eval(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Load more claims')`), false);
  // one claim's evidence, read for that claim only
  await page.eval(`[...document.querySelectorAll('.large-claim button')][0].click()`);
  await page.until(`document.body.innerText.includes('Every mapped row was counted.')`, 6000);
  assert.ok(calls.some(call => call.path === `/v1/workspaces/a/results/${LARGE}/evidence/${LARGE_EVIDENCE}`));
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-large-result.png"), true);
  // long IDs and claim text wrap on a phone instead of scrolling the page sideways
  await page.resize(390, 844);
  assert.ok(await page.eval("document.documentElement.scrollWidth <= 390"), "no horizontal scroll at 390px");
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-large-result-390.png"), true);
  await page.resize(1440, 900);
  // a search is its own sequence, and its unknown total is not shown as a number
  await page.set('[aria-label="Search claims"]', "spike");
  await page.click('form[role=search] button[type=submit]');
  await page.until(`document.body.innerText.includes('Showing 1 matching “spike”.')`, 6000);
  assert.equal(calls.filter(call => call.path === `/v1/workspaces/a/results/${LARGE}`).length, 1, "the whole result is asked for once, not retried");
  assert.deepEqual(mutations(), [], "reading in pages sends no mutation");
});

test("Chrome shows how far a delete has gone (K13), never more than the service reports, and a cancelled dialog sends nothing", async () => {
  reset(); bindings = "on"; deletionStages = ["retained", "purged"];
  const text = () => page.eval("document.body.innerText");
  await page.open(base + "#/retention?" + resultQuery());
  await page.until(`[...document.querySelectorAll('.btn--danger')].some(b => !b.disabled)`);
  // Cancel closes the dialog without a request
  await page.eval(`[...document.querySelectorAll('.btn--danger')].find(b => !b.disabled).click()`);
  await page.until(`document.querySelector('[role=dialog]')`);
  await page.eval(`[...document.querySelectorAll('[role=dialog] button')].find(b => b.textContent === 'Cancel').click()`);
  await page.until(`!document.querySelector('[role=dialog]')`);
  assert.equal(calls.some(call => call.method === "DELETE"), false, "a cancelled dialog sends no DELETE");
  // the delete itself, then its status, read once and again on request
  await page.eval(`[...document.querySelectorAll('.btn--danger')].find(b => !b.disabled).click()`);
  await page.until(`document.querySelector('[role=dialog] input[type=checkbox]')`);
  await page.click('[role=dialog] input[type=checkbox]');
  await page.eval(`[...document.querySelectorAll('[role=dialog] button')].find(b => b.textContent.includes('Delete project')).click()`);
  await page.until(`document.body.innerText.includes('Retained; eligible for removal after the retention period')`, 6000);
  assert.match(await text(), /Not yet/);
  assert.match(await text(), /when the delete was committed/);
  assert.match(await text(), /Neither is erased here/);
  assert.equal(calls.filter(call => call.path.endsWith("/deletion-status")).length, 1, "read once, not polled");
  await page.eval(`[...document.querySelectorAll('.deletion-status button')].find(b => b.textContent === 'Check again').click()`);
  await page.until(`document.body.innerText.includes("This project's records are removed")`, 6000);
  const after = await text();
  assert.match(after, /2030-02-01 00:00 UTC/);
  assert.match(after, /from a restore checkpoint, not the delete itself/, "the time basis is said, not read as the delete's own time");
  assert.match(after, /Neither is erased here/, "purged never claims the raw sources or backups");
  assert.doesNotMatch(after, /everything|all data/i);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-deletion-status.png"), true);
});

/* K16: a lazy screen whose code cannot load (404 or a dropped connection) shows a fixed message with a way
   back, never an empty root; on signed-in screens the session header and SessionGate stay outside it. */
const FAILED = "This screen could not be shown";
const rootState = () => page.eval(`({ children: document.getElementById('root').childElementCount,
  text: document.body.innerText, alert: !!document.querySelector('[role=alert] h1'),
  header: !!document.querySelector('[aria-label="Account and workspace"]'), gate: !!document.querySelector('.session-app') })`);
for (const [name, offline] of [["HostedApp", false], ["WorkspaceManager", false], ["WorkspaceManager", true], ["HostedConnect", false], ["LargeResult", true]]) {
  test(`Chrome recovers from a missing ${name} chunk (${offline ? "offline" : "404"}) without a blank root`, async () => {
    reset(); missingChunk = { name, offline };
    if (name === "LargeResult") { bindings = "on"; large = true; }
    await page.open(base + (name === "LargeResult" ? "#/investigation?" + resultQuery(LARGE) : "#/workspaces"));
    if (name === "HostedConnect") {
      await page.until(`document.body.innerText.includes("Synthetic workspace")`);
      await page.clickText("Open workspace");
      await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Connect project')`);
      await page.clickText("Connect project");
    }
    await page.until(`document.body.innerText.includes(${JSON.stringify(FAILED)})`, 6000);
    const failed = await rootState();
    assert.ok(failed.children > 0 && failed.alert, "a fixed alert, not an empty root");
    assert.match(failed.text, /does not undo anything you already submitted/);
    assert.doesNotMatch(failed.text, /dynamically imported|Failed to fetch|TypeError|\.js\b|assets\//i, "the error's own text is never shown");
    // signed-in screens keep the session header and the gate around the failed screen
    assert.equal(failed.header && failed.gate, name !== "HostedApp");
    if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, `chunk-${name}-${offline ? "offline" : "404"}.png`), true);
    missingChunk = null;
    if (name === "HostedConnect") {
      // leaving the screen clears its failure; nothing else had failed
      await page.clickText("Workspaces");
      await page.until(`document.body.innerText.includes('Workspace access') && !document.body.innerText.includes(${JSON.stringify(FAILED)})`);
      return;
    }
    await page.clickText("Reload");
    await page.until(name === "LargeResult" ? `document.body.innerText.includes('Showing 2 of 120,000.')`
      : `document.body.innerText.includes('Workspace access')`, 6000);
    assert.doesNotMatch(await page.eval("document.body.innerText"), new RegExp(FAILED));
    assert.deepEqual(mutations(), [], "recovery sends no mutation");
  });
}

test("Chrome reads the lineage of a large result in pages (K10): merged by ID, one restart, a refusal and its retry, whole only at the header's counts", async () => {
  reset(); bindings = "on"; large = true;
  graphFaults = [{ cursor: "synthetic-page-5", code: "cursor_expired", status: 410 }, { cursor: "synthetic-page-20", code: "service_unavailable", status: 503 }];
  const text = () => page.eval("document.body.innerText");
  await page.open(base + "#/investigation?" + resultQuery(LARGE));
  await page.until(`document.body.innerText.includes('Read the lineage in pages')`, 6000);
  const more = label => page.eval(`(() => { const b = [...document.querySelectorAll('.large-graph button')].find(b => b.textContent === ${JSON.stringify(label)}); if (!b) return false; b.click(); return true; })()`);
  const idle = () => page.until(`!document.querySelector('.large-graph button[aria-busy=true]') && !document.querySelector('.large-graph [aria-disabled=true]')`, 6000);
  assert.ok(await more("Read the lineage")); await idle();
  let restarted = false, refused = false;
  for (let presses = 0; presses < 80; presses++) {
    await idle();
    restarted ||= /started again from the first page/.test(await text());
    if (!refused && /could not|unavailable/i.test(await page.eval(`document.querySelector('.large-graph').innerText`))) {
      refused = true; // the refusal is shown, and pressing again asks for the same page
    }
    if (!(await more("Load more of the lineage"))) break;
  }
  assert.ok(restarted, "an expired cursor starts again from the first page");
  assert.ok(refused, "a refused page says so and keeps the place");
  await page.until(`document.body.innerText.includes('Read 22 of 22 nodes and 30 of 30 edges. That is the whole lineage.')`, 6000);
  assert.equal(await page.eval(`document.querySelectorAll('.large-graph-edges li').length`), 30);
  assert.match(await text(), /Claim .+ cites Evidence/);
  const graphCalls = calls.filter(call => call.path.endsWith("/graph"));
  assert.ok(graphCalls.every(call => /^\/v1\/workspaces\/a\/results\/p6-result-9{64}\/graph$/.test(call.path)));
  assert.deepEqual(mutations(), [], "reading the lineage sends no mutation");
  await page.eval("document.querySelector('.large-graph').scrollIntoView(); true");
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-large-lineage.png"), true);
  await page.resize(390, 844);
  assert.ok(await page.eval("document.documentElement.scrollWidth <= 390"), "no horizontal scroll at 390px");
  await page.eval("document.querySelector('.large-graph').scrollIntoView(); true");
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-large-lineage-390.png"), true);
  await page.resize(1440, 900);
});

/* K09: job controls appear only where the workspace offers them, and say no more than the service does. */
async function analyzeFrom(question = "What is observed?") {
  await connect(); await fillMapping(true);
  await page.clickText("Confirm mapping and save snapshot");
  await page.until("document.querySelector('#hosted-first-question')");
  await page.eval(`(() => { const e = document.querySelector('#hosted-first-question'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(e, ${JSON.stringify(question)}); e.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await page.clickText("Analyze saved snapshot");
}
test("Chrome K09: a failed analysis shows its attempts and is retried, because the service allows it, as one new linked job whose result opens", async () => {
  reset(); jobControls = true; jobScript.set(analysis.job_id, { states: ["running", "failed"], at: 0, retryable: true });
  await analyzeFrom();
  await page.until(`document.body.innerText.includes('ended as failed')`, 8000);
  await page.until(`document.body.innerText.includes('This job can be retried once, as a new linked job.')`, 6000);
  await page.clickText("Show attempts");
  await page.until(`document.body.innerText.includes('Attempt 1: failed, a temporary failure. Started 08:00:00 UTC, ended 08:00:06 UTC.')`, 6000);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-k09-retry-offered.png"), true);
  await page.clickText("Retry job");
  await page.until(`document.querySelector('.verdict')`, 10000);
  assert.match(await page.eval("location.hash"), /job=job_retry_/);
  const retries = calls.filter(call => call.path.endsWith("/retry"));
  assert.equal(retries.length, 1, "one press, one retry");
  assert.equal(retries[0].text, "{}"); assert.equal(retries[0].headers["x-csrf-token"], "synthetic-csrf-only");
  assert.match(retries[0].headers["idempotency-key"], /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(submissions.filter(value => value.operation === "analyze").length, 1, "a retry is not a second analysis submission");
});

test("Chrome K09: cancelling a running job is a request, watched until the service reports it cancelled; no retry is offered when the service says no", async () => {
  reset(); jobControls = true; jobScript.set(analysis.job_id, { states: ["running"], at: 0, retryable: false });
  await analyzeFrom();
  await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Cancel job')`, 6000);
  await page.clickText("Cancel job");
  await page.until(`document.body.innerText.includes('Cancel requested. The job has not stopped yet')`, 6000);
  await page.until(`document.body.innerText.includes('ended as cancelled')`, 8000);
  await page.until(`document.body.innerText.includes('This job cannot be retried')`, 6000);
  assert.equal(await page.eval(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry job')`), false);
  const cancels = calls.filter(call => call.path.endsWith("/cancel"));
  assert.equal(cancels.length, 1); assert.equal(cancels[0].text, "{}");
  assert.equal(cancels[0].headers["x-csrf-token"], "synthetic-csrf-only"); assert.equal(cancels[0].headers["idempotency-key"], undefined);
  assert.equal(calls.some(call => call.path.endsWith("/retry")), false);
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-k09-cancelled.png"), true);
});

test("Chrome K09: without the job features, no control is shown and none is called", async () => {
  reset(); jobScript.set(analysis.job_id, { states: ["running", "failed"], at: 0, retryable: true });
  await analyzeFrom();
  await page.until(`document.body.innerText.includes('ended as failed')`, 8000);
  assert.equal(await page.eval(`!!document.querySelector('.job-controls')`), false);
  assert.equal(calls.some(call => /\/(cancel|retry|attempts)$/.test(call.path)), false);
});

/* Network waterfall (Kiên, polling/server-cũ 13/11): what a tab sends while a person only reads or waits.
   Each case writes its counts before asserting, so the same file records the baseline and the fixed build. */
const kindOf = path => /\/session$/.test(path) ? "session" : /\/capabilities$/.test(path) ? "capabilities" : /\/header$/.test(path) ? "result_header"
  : /\/jobs\/[^/]+\/output$/.test(path) ? "job_output" : /\/jobs\/[^/]+$/.test(path) ? "job" : /\/results\/p6-result-[0-9a-f]{64}$/.test(path) ? "result_full" : "other";
function waterfall(name, from, to, extra = {}) {
  const window = calls.filter(call => call.at >= from && call.at <= to), seconds = (to - from) / 1000, by = {};
  for (const call of window) by[kindOf(call.path)] = (by[kindOf(call.path)] ?? 0) + 1;
  const record = { case: name, seconds: Number(seconds.toFixed(2)), requests: window.length, per_second: Number((window.length / seconds).toFixed(2)), by_route: by,
    sequence: window.map(call => [Number(((call.at - from) / 1000).toFixed(2)), call.method, kindOf(call.path)]), ...extra };
  if (process.env.E2E_EVIDENCE_DIR) writeFileSync(resolve(process.env.E2E_EVIDENCE_DIR, `waterfall-${name}.json`), JSON.stringify(record, null, 1));
  return record;
}
const idle = ms => new Promise(done => setTimeout(done, ms));

test("waterfall: an open result that a person only reads is confirmed by its own header, not by a finished job", async () => {
  reset();
  await page.open(base + "#/investigation?" + resultQuery());
  await page.until(`document.querySelector('.verdict')`, 6000);
  const from = performance.now(); await idle(12_000); const to = performance.now();
  const w = waterfall("result-idle-12s", from, to, { visible: await page.eval("document.visibilityState") });
  assert.equal(await page.eval(`!!document.querySelector('.session-cover')`), false, "a confirmed result stays readable");
  assert.equal(w.by_route.job ?? 0, 0, "no read of the finished job");
  assert.equal((w.by_route.job_output ?? 0) + (w.by_route.result_full ?? 0), 0, "no whole-result read on a timer");
  assert.ok((w.by_route.result_header ?? 0) >= 3, "the header confirms the result within each lease");
});

test("waterfall: while a job is awaited, its own polling confirms it; the job is not read twice", async () => {
  reset(); jobScript.set(analysis.job_id, { doneAt: Infinity, retryable: null });
  await analyzeFrom();
  // running for 8 s from its acceptance, whatever reads it in between
  await page.until(`document.body.innerText.includes('Waiting for analyze')`, 6000);
  jobScript.get(analysis.job_id).doneAt = calls.find(call => call.method === "POST" && call.text.includes('"operation":"analyze"')).at + 8000;
  await page.until(`document.querySelector('.verdict')`, 20_000);
  await idle(500);
  const from = calls.find(call => call.method === "POST" && call.text.includes('"operation":"analyze"')).at;
  const w = waterfall("job-pending", from, performance.now(), { job_running_ms: 8000, job_poll_min_ms: 1000 });
  assert.ok((w.by_route.job ?? 0) <= 10, `job reads ${w.by_route.job}: about one per second of polling, none added by the access check`);
  assert.equal(w.by_route.job_output ?? 0, 1, "the finished analysis is read once, not again by the result screen");
});

test("waterfall: a server without the header route covers the result and reads nothing on a timer", async () => {
  reset(); paged = false;
  await page.open(base + "#/investigation?" + resultQuery());
  await page.until(`document.querySelector('.verdict') || document.body.innerText.includes('cannot confirm access to a single result')`, 6000);
  const from = performance.now(); await idle(12_000); const to = performance.now();
  const w = waterfall("legacy-result-12s", from, to);
  assert.match(await page.eval("document.body.innerText"), /cannot confirm access to a single result/);
  assert.equal(await page.eval(`!!document.querySelector('.verdict')`), false, "no result content");
  assert.equal((w.by_route.job_output ?? 0) + (w.by_route.result_full ?? 0) + (w.by_route.job ?? 0), 0, "nothing read in full on a timer");
  if (process.env.E2E_EVIDENCE_DIR) await page.shot(resolve(process.env.E2E_EVIDENCE_DIR, "hosted-legacy-result-covered.png"), true);
  // checking again reloads capabilities only; once the server offers the header, the result opens
  paged = true; const before = calls.length;
  await page.clickText("Check again");
  await page.until(`document.querySelector('.verdict')`, 6000);
  assert.ok(calls.slice(before).some(call => call.path.endsWith("/capabilities")));
});

/* Hidden is emulated through the Page Visibility API (headless Chrome has no real background tab here):
   document.visibilityState is overridden and visibilitychange dispatched, as a real tab switch would. */
const setHidden = hidden => page.eval(`(() => { Object.defineProperty(document, "visibilityState", { configurable: true, get: () => ${JSON.stringify(hidden ? "hidden" : "visible")} });
  document.dispatchEvent(new Event("visibilitychange")); return true; })()`);
test("waterfall: a hidden tab reads nothing, waiting job included; shown again it covers until a fresh check, then carries on", async () => {
  reset(); jobScript.set(analysis.job_id, { doneAt: Infinity, retryable: null });
  await analyzeFrom();
  await page.until(`document.body.innerText.includes('Waiting for analyze')`, 6000);
  await idle(1500);
  await setHidden(true);
  const from = performance.now(); await idle(12_000); const to = performance.now();
  const hidden = waterfall("hidden-12s-job-pending", from, to, { visibility: "emulated hidden" });
  assert.equal(hidden.requests, 0, "no poll and no access check while hidden");
  jobScript.get(analysis.job_id).doneAt = 0;
  const back = performance.now();
  // watch for the cover from before the tab is shown, so a quick answer cannot hide that it was there
  await page.eval(`(() => { window.__covered = false; window.__contentWhileUnconfirmed = false;
    new MutationObserver(() => { if (document.querySelector('.session-cover')) window.__covered = true;
      if (!window.__covered && document.querySelector('.verdict')) window.__contentWhileUnconfirmed = true; })
      .observe(document.body, { childList: true, subtree: true, attributes: true }); return true; })()`);
  await setHidden(false);
  await page.until(`!document.querySelector('.session-cover') && document.querySelector('.verdict')`, 10_000);
  assert.equal(await page.eval("window.__covered"), true, "covered on return: the lease ran out while hidden");
  assert.equal(await page.eval("window.__contentWhileUnconfirmed"), false, "the result never appeared before access was confirmed");
  const shown = waterfall("shown-again-to-result", back, performance.now());
  assert.ok(shown.sequence.findIndex(([, , kind]) => kind === "session") <= shown.sequence.findIndex(([, , kind]) => kind === "job_output"),
    "access is checked before the finished job's output is opened");
  assert.equal(submissions.filter(value => value.operation === "analyze").length, 1, "the job is not resubmitted");
});

test("waterfall: the workspace screen, open and idle, sends reads only (measured for the P01–P38 workload table)", async () => {
  reset();
  await page.open(base + "#/workspaces");
  await page.until(`document.body.innerText.includes("Synthetic workspace")`);
  const clicked = performance.now();
  await page.clickText("Open workspace");
  await page.until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Connect project')`);
  await idle(1000);
  const from = performance.now(); await idle(12_000); const to = performance.now();
  const w = waterfall("workspace-idle-12s", from, to);
  assert.ok(w.requests > 0 && calls.filter(call => call.at >= from && call.at <= to).every(call => call.method === "GET"), "an idle screen only reads");
  // FL-118: the screen runs no timer of its own; only the guard's check (session + capabilities, every 2.4–3.6 s) reads
  assert.deepEqual(Object.keys(w.by_route).sort(), ["capabilities", "session"]);
  assert.ok(w.requests <= 2 * Math.ceil(12 / 2.4), `idle reads stay within the guard's own checks (${w.requests})`);
  const opened = calls.filter(call => call.at >= clicked && call.at < from);
  assert.equal(opened.filter(call => call.path === "/v1/workspaces").length, 1, "opening a workspace reads the workspace list once");
});
