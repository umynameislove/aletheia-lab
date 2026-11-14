/* The four hosted bindings Kiên handed over on 2026-11-11 (HOP-DONG-4-BINDING.txt): refresh, follow-up,
   export and logical delete. Each runs through the real hosted client and port over a scripted
   same-origin fetch, so routes, headers, bodies, CAS, keys and every scope check are the shipped code's.
   The parent view is the producer's own hosted v2 fixture (fixtures/metric-dimensions-hosted-v2). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHostedClient, HostedRequestDiscarded } from "../src/service/hosted-client.ts";
import { bindingOff, createHostedPort } from "../src/service/hosted-port.ts";
import { readProblem, BINDING_PROBLEM_STATUS } from "../src/service/hosted.ts";
import { isTombstone } from "../src/service/port.ts";

const VERSION = "product-api/v1-draft", WS = "a";
const PARENT = JSON.parse(readFileSync(fileURLToPath(new URL("../../../fixtures/metric-dimensions-hosted-v2/view.json", import.meta.url)), "utf8"));
const PROJECT = PARENT.project.id, SNAPSHOT = PARENT.snapshot.id, RESULT = PARENT.result.id;
const CLAIM = PARENT.claims[0].id, NODE = PARENT.graph.nodes[0].id;
const CHILD = "p6-result-" + "c".repeat(64), NEW_SNAPSHOT = "upload-snapshot-" + "e".repeat(64);
const TOMBSTONE = "p6-deletion-" + "d".repeat(64);
const session = { user_id: "actor1", context_id: "ctx1", csrf_token: "synthetic-csrf-token" };
const FEATURES = { "projects.refresh": true, "projects.follow_up": true, "exports.download": true, "projects.logical_delete": true, "catalog.read": true };
const caps = (over = {}) => ({ workspace_id: WS, workspace_name: "Synthetic", role: "owner", membership_revision: 9,
  analysis_backends: ["local_evidence"], supported_source_types: ["zip_bundle"], supported_export_formats: ["json", "markdown"],
  allowed_actions: ["read", "review", "export", "delete", "import", "analyze"], features: { ...FEATURES },
  question_chars: 2000, upload_bundle_bytes: 1024, page_max: 200, job_poll_min_ms: 1, ...over });
const json = (value, status = 200, type = "application/json") => new Response(JSON.stringify(value), { status, headers: { "Content-Type": type } });
const problem = (code, status) => json({ schema_version: VERSION, type: "urn:test:" + code, title: "Refused", status, code,
  detail: "Safe synthetic refusal.", request_id: "req_synthetic" }, status, "application/problem+json");
const details = (revision = 3, state = "active") => ({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, revision,
  display_name: "Synthetic", state, current_snapshot_id: SNAPSHOT, current_result_id: RESULT });
const receipt = job => ({ schema_version: VERSION, workspace_id: WS, job_id: job, status: "queued", request_id: "req1", status_url: `/v1/workspaces/${WS}/jobs/${job}` });
const done = (job, status = "succeeded") => ({ schema_version: VERSION, workspace_id: WS, job_id: job, status, progress: null });
const childView = (over = {}) => {
  const view = structuredClone(PARENT);
  view.result.id = CHILD;
  view.conversation.turns.push({ ...view.conversation.turns[0], id: "p6-turn-" + "f".repeat(64), result_id: CHILD, question: "Why this claim?" });
  return Object.assign(view, over);
};
const sha = bytes => createHash("sha256").update(bytes).digest("hex");

/* A scripted server: each route answers from `routes`, keyed "METHOD path" (query stripped), in order. */
function server(routes) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body });
    const key = `${init.method} ${url.split("?")[0]}`;
    const answers = routes[key];
    assert.ok(answers && answers.length, `unexpected ${key}`);
    const answer = answers.length > 1 ? answers.shift() : answers[0];
    return typeof answer === "function" ? answer(url, init) : answer.clone();
  };
  return { calls, fetch };
}
function setup(routes, { over = {}, isCurrent = () => true, view = PARENT } = {}) {
  const s = server(routes), shown = [];
  const client = createHostedClient({ session, isCurrent, fetch: s.fetch });
  const port = createHostedPort();
  port.setContext({ session, workspace: WS, caps: caps(over), client, isCurrent, initialView: view, initialJobId: "job0", onShown: scope => shown.push(scope) });
  return { port, shown, ...s };
}
const B = `/v1/workspaces/${WS}`;

test("each binding is offered only by its workspace feature and the role's actions", () => {
  assert.equal(bindingOff(null, "refresh"), "Open a workspace first.");
  for (const binding of ["refresh", "follow_up", "export", "delete"]) assert.equal(bindingOff(caps(), binding), null, binding);
  assert.match(bindingOff(caps({ features: { ...FEATURES, "projects.refresh": false } }), "refresh"), /does not offer/);
  assert.match(bindingOff(caps({ features: {} }), "export"), /does not offer/);
  assert.match(bindingOff(caps({ allowed_actions: ["read", "export"] }), "follow_up"), /role/);
  assert.match(bindingOff(caps({ allowed_actions: ["read", "analyze"] }), "refresh"), /role/, "refresh also uploads, so it needs import");
  assert.match(bindingOff(caps({ allowed_actions: ["read"] }), "delete"), /role/);
});

test("a binding that is off fails before any request, whatever the build", async () => {
  const { port, calls } = setup({}, { over: { features: { "catalog.read": true } } });
  await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "feature_unavailable" });
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why?"), { code: "feature_unavailable" });
  await assert.rejects(port.export_report(RESULT, "json"), { code: "feature_unavailable" });
  await assert.rejects(port.delete_project(PROJECT), { code: "feature_unavailable" });
  await assert.rejects(port.uploadReplacement(PROJECT, new Blob(["zip"])), { code: "feature_unavailable" });
  assert.deepEqual(calls, []);
});

test("R1 refresh uploads the chosen ZIP, sends CAS and key, and returns the RefreshResult without touching the open result", async () => {
  for (const status of ["new_snapshot", "unchanged"]) {
    const snapshot = status === "unchanged" ? SNAPSHOT : NEW_SNAPSHOT;
    const { port, calls } = setup({
      [`POST ${B}/sources`]: [json({ schema_version: VERSION, workspace_id: WS, source_id: "src_new", sha256: "b".repeat(64) }, 201)],
      [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
      [`POST ${B}/projects/${PROJECT}/refresh`]: [json(receipt("job_r"), 202)],
      [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
      [`GET ${B}/jobs/job_r/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_r", operation: "refresh",
        result: { project_id: PROJECT, status, previous_snapshot_id: SNAPSHOT, snapshot_id: snapshot, revision: 4 } })],
    });
    const source = await port.uploadReplacement(PROJECT, new Blob(["PK synthetic"], { type: "application/zip" }));
    assert.deepEqual(source, { source_id: "src_new" });
    const outcome = await port.refresh(PROJECT, source);
    assert.deepEqual(outcome, { project_id: PROJECT, snapshot_id: snapshot, status, previous_snapshot_id: SNAPSHOT, revision: 4 });
    const post = calls.find(call => call.url.endsWith("/refresh"));
    assert.equal(post.method, "POST");
    assert.deepEqual(JSON.parse(post.body), { schema_version: VERSION, source_id: "src_new" });
    assert.equal(post.headers["If-Match"], '"3"');
    assert.match(post.headers["Idempotency-Key"], /^[A-Za-z0-9_-]{16,128}$/);
    assert.equal(post.headers["X-CSRF-Token"], "synthetic-csrf-token");
    // the open result stays on its own snapshot; a new snapshot is analysed only when someone asks
    const open = await port.demo_view();
    assert.equal(open.result.id, RESULT); assert.equal(open.snapshot.id, SNAPSHOT);
    assert.equal(calls.filter(call => call.url.endsWith("/jobs")).length, 0, "refresh never starts an analysis");
  }
});

test("R2 refresh without a chosen replacement never guesses a source or reuses the old one", async () => {
  const { port, calls } = setup({});
  await assert.rejects(port.refresh(PROJECT), { code: "invalid_request" });
  await assert.rejects(port.refresh(PROJECT, { source_id: "../other" }), { code: "invalid_request" });
  assert.deepEqual(calls, []);
});

test("R3 a stale CAS is reported, the revision is read again next time, and nothing is incremented by the client", async () => {
  const { port, calls } = setup({
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3)), json(details(5))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [problem("revision_conflict", 412), json(receipt("job_r"), 202)],
    [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
    [`GET ${B}/jobs/job_r/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_r", operation: "refresh",
      result: { project_id: PROJECT, status: "new_snapshot", previous_snapshot_id: SNAPSHOT, snapshot_id: NEW_SNAPSHOT, revision: 6 } })],
  });
  await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "revision_conflict" });
  await port.refresh(PROJECT, { source_id: "src_new" });
  const posts = calls.filter(call => call.url.endsWith("/refresh"));
  assert.deepEqual(posts.map(call => call.headers["If-Match"]), ['"3"', '"5"']);
  assert.notEqual(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"], "a reviewed retry is a new intent");
  assert.equal(calls.filter(call => call.url === `${B}/projects/${PROJECT}`).length, 2);
});

test("R4 a lost refresh answer keeps the same key, body and If-Match, so the retry reconciles", async () => {
  let first = true;
  const { port, calls } = setup({
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [() => { if (first) { first = false; throw new TypeError("connection reset"); } return json(receipt("job_r"), 202); }],
    [`GET ${B}/jobs/job_r`]: [json(done("job_r", "failed"))],
  });
  await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "outcome_unknown" });
  // a failed refresh job (for example a ZIP whose unit no longer matches) leaves the old snapshot as it was
  await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "refresh_failed" });
  const posts = calls.filter(call => call.url.endsWith("/refresh"));
  assert.equal(posts.length, 2);
  assert.equal(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"]);
  assert.equal(posts[0].body, posts[1].body);
  assert.equal(posts[0].headers["If-Match"], posts[1].headers["If-Match"]);
  assert.equal(calls.filter(call => call.url === `${B}/projects/${PROJECT}`).length, 1, "the held revision is reused, not re-read");
  assert.equal((await port.demo_view()).snapshot.id, SNAPSHOT);
});

test("R5 a refresh answer about another project, or an inconsistent one, is refused", async () => {
  for (const [result, code] of [
    [{ project_id: "p3-project-" + "0".repeat(64), status: "new_snapshot", previous_snapshot_id: SNAPSHOT, snapshot_id: NEW_SNAPSHOT, revision: 4 }, "response_out_of_scope"],
    [{ project_id: PROJECT, status: "unchanged", previous_snapshot_id: SNAPSHOT, snapshot_id: NEW_SNAPSHOT, revision: 4 }, "response_invalid"],
    [{ project_id: PROJECT, status: "new_snapshot", previous_snapshot_id: SNAPSHOT, snapshot_id: SNAPSHOT, revision: 4 }, "response_invalid"],
  ]) {
    const { port } = setup({
      [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
      [`POST ${B}/projects/${PROJECT}/refresh`]: [json(receipt("job_r"), 202)],
      [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
      [`GET ${B}/jobs/job_r/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_r", operation: "refresh", result })],
    });
    await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code });
  }
});

test("R6 the project revision comes from ProjectPage when the details route answers with a canonical record", async () => {
  const { port, calls, shown } = setup({
    [`GET ${B}/projects/${PROJECT}`]: [json({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, record_id: "rec1", record_kind: "project_state", data: {} })],
    [`GET ${B}/projects`]: [
      json({ schema_version: VERSION, workspace_id: WS, items: [{ project_id: "p3-project-" + "1".repeat(64), revision: 1 }], next_cursor: "c2", total: null, total_kind: "unknown" }),
      json({ schema_version: VERSION, workspace_id: WS, items: [{ project_id: PROJECT, revision: 7 }], next_cursor: null, total: null, total_kind: "unknown" }),
    ],
    [`DELETE ${B}/projects/${PROJECT}`]: [json({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, revision: 8,
      tombstone_id: TOMBSTONE, status: "tombstoned", retention: "retained" })],
  });
  assert.deepEqual(shown, [{ resultId: PARENT.result.id, projectId: PARENT.project.id, snapshotId: PARENT.snapshot.id }],
    "the result on screen is named for its own access check (K15)");
  assert.equal((await port.delete_project(PROJECT)).revision, 8);
  assert.equal(shown.at(-1), null, "after the delete only the receipt is shown, so the result's read stops");
  assert.equal(calls.find(call => call.method === "DELETE").headers["If-Match"], '"7"');
  assert.match(calls[2].url, /cursor=c2/);
  const deleting = setup({ [`GET ${B}/projects/${PROJECT}`]: [json(details(3, "deleting"))] });
  await assert.rejects(deleting.port.delete_project(PROJECT), { code: "not_found" });
  assert.equal(deleting.calls.some(call => call.method === "DELETE"), false);
});

const followRoutes = (summary = {}, child = childView()) => ({
  [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
  [`POST ${B}/results/${RESULT}/follow-ups`]: [json(receipt("job_f"), 202)],
  [`GET ${B}/jobs/job_f`]: [json(done("job_f"))],
  [`GET ${B}/jobs/job_f/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_f", operation: "follow_up",
    result: { project_id: PROJECT, result_id: CHILD, parent_result_id: RESULT, snapshot_id: SNAPSHOT, revision: 4, ...summary } })],
  [`GET ${B}/results/${CHILD}`]: [json({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, result_id: CHILD, view: child })],
});

test("F1 a follow-up on the open result's claim reads its summary, then the child's own ResultView", async () => {
  for (const [kind, selection] of [["claim", CLAIM], ["node", NODE]]) {
    const { port, calls } = setup(followRoutes());
    const child = await port.follow_up(RESULT, kind, selection, "Why this claim?");
    assert.equal(child.result.id, CHILD);
    assert.ok(child.conversation.turns.some(turn => turn.result_id === RESULT), "the parent's turns are kept");
    assert.equal(port.latestJobId(), "job_f");
    const post = calls.find(call => call.url.endsWith("/follow-ups"));
    assert.deepEqual(JSON.parse(post.body), { schema_version: VERSION, selection_kind: kind, selection_id: selection, question: "Why this claim?" });
    assert.equal(post.headers["If-Match"], '"3"');
    assert.match(post.headers["Idempotency-Key"], /^[A-Za-z0-9_-]{16,128}$/);
    assert.deepEqual(calls.map(call => call.url).slice(-2), [`${B}/jobs/job_f/output`, `${B}/results/${CHILD}`]);
    // the child is now a result of its own: reopening it reads GET results, never an analysis output
    assert.equal((await port.view(CHILD)).result.id, CHILD);
    assert.equal(calls.at(-1).url, `${B}/results/${CHILD}`);
  }
});

test("F2 a selection outside the open result, or no open parent, sends nothing", async () => {
  const { port, calls } = setup(followRoutes());
  await assert.rejects(port.follow_up(RESULT, "claim", "p6-claim-other", "Why?"), { code: "selection_not_available" });
  await assert.rejects(port.follow_up(RESULT, "node", CLAIM, "Why?"), { code: "selection_not_available" });
  await assert.rejects(port.follow_up(RESULT, "edge", CLAIM, "Why?"), { code: "selection_not_available" });
  await assert.rejects(port.follow_up(CHILD, "claim", CLAIM, "Why?"), { code: "no_result" });
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, " padded "), { code: "invalid_question" });
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "x".repeat(2001)), { code: "invalid_question" });
  assert.deepEqual(calls, []);
});

test("F3 a summary is never read as a view, and a child from another parent, snapshot or result is refused", async () => {
  for (const [routes, code] of [
    [followRoutes({ parent_result_id: "p6-result-" + "9".repeat(64) }), "response_out_of_scope"],
    [followRoutes({ snapshot_id: NEW_SNAPSHOT }), "response_out_of_scope"],
    [followRoutes({ result_id: RESULT }), "response_invalid"],
    [{ ...followRoutes(), [`GET ${B}/jobs/job_f/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_f", operation: "follow_up", result: childView() })] }, "response_invalid"],
    [followRoutes({}, { ...childView(), snapshot: { ...PARENT.snapshot, id: NEW_SNAPSHOT } }), "response_out_of_scope"],
    [followRoutes({}, { ...childView(), conversation: { ...PARENT.conversation, turns: [{ ...PARENT.conversation.turns[0], result_id: CHILD }] } }), "response_out_of_scope"],
  ]) {
    const { port } = setup(routes);
    await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), { code });
    assert.equal((await port.demo_view()).result.id, RESULT, "the parent stays the open result");
  }
});

test("F4 a context change while the follow-up runs discards its late answer", async () => {
  let current = true;
  const routes = followRoutes();
  routes[`GET ${B}/jobs/job_f`] = [() => { current = false; return json(done("job_f")); }];
  const { port, calls } = setup(routes, { isCurrent: () => current });
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), HostedRequestDiscarded);
  assert.equal(calls.some(call => call.url === `${B}/results/${CHILD}`), false);
});

const BYTES = new TextEncoder().encode('{"synthetic":true}\n');
const DURABLE = `ex_json_${RESULT}_${"a".repeat(32)}`;
const descriptor = (over = {}) => ({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, result_id: RESULT, export_id: DURABLE,
  format: "json", byte_size: BYTES.length, sha256: sha(BYTES), download_url: `${B}/exports/${DURABLE}/download`,
  expires_at: "2099-01-01T00:00:00Z", ...over });
const download = (bytes = BYTES, type = "application/json") => new Response(bytes, { status: 200, headers: { "Content-Type": type } });

test("E1 export creates a descriptor, then downloads exactly the described bytes", async () => {
  const { port, calls } = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [json(descriptor(), 201)],
    [`GET ${B}/exports/${DURABLE}/download`]: [download()],
  });
  assert.deepEqual(await port.export_report(RESULT, "json"), BYTES);
  assert.deepEqual(JSON.parse(calls[0].body), { format: "json" }, "no schema_version in an export request");
  assert.match(calls[0].headers["Idempotency-Key"], /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(calls[0].headers["If-Match"], undefined, "export takes no CAS");
  assert.equal(calls[1].method, "GET");
  const md = new TextEncoder().encode("# Report\n");
  const markdown = `ex_markdown_${RESULT}`;
  const legacy = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [json(descriptor({ export_id: markdown, format: "markdown", byte_size: md.length, sha256: sha(md),
      download_url: `${B}/exports/${markdown}/download`, expires_at: null }), 201)],
    [`GET ${B}/exports/${markdown}/download`]: [download(md, "text/markdown; charset=utf-8")],
  });
  assert.deepEqual(await legacy.port.export_report(RESULT, "markdown"), md, "a legacy descriptor is downloaded at once, not kept");
});

test("E5 an advertised PDF (K12) is created and downloaded as application/pdf, and nothing else passes as it", async () => {
  const pdf = new TextEncoder().encode("%PDF-1.4 synthetic\n%%EOF\n");
  const id = `ex_pdf_${RESULT}_${"b".repeat(32)}`;
  const answer = descriptor({ export_id: id, format: "pdf", byte_size: pdf.length, sha256: sha(pdf), download_url: `${B}/exports/${id}/download` });
  const over = { over: { supported_export_formats: ["json", "markdown", "pdf"] } };
  const { port, calls } = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [json(answer, 201)],
    [`GET ${B}/exports/${id}/download`]: [download(pdf, "application/pdf")],
  }, over);
  assert.deepEqual(await port.export_report(RESULT, "pdf"), pdf);
  assert.deepEqual(JSON.parse(calls[0].body), { format: "pdf" });
  const mislabelled = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [json(answer, 201)],
    [`GET ${B}/exports/${id}/download`]: [download(pdf, "application/json")],
  }, over);
  await assert.rejects(mislabelled.port.export_report(RESULT, "pdf"), { code: "response_invalid" });
  const swapped = setup({ [`POST ${B}/results/${RESULT}/exports`]: [json(answer, 201)] }, over);
  await assert.rejects(swapped.port.export_report(RESULT, "json"), { code: "outcome_unknown" }, "a PDF descriptor never answers a JSON request");
  assert.equal(swapped.calls.length, 1);
});

test("E2 only advertised formats are asked for; PDF stays off until the workspace advertises it", async () => {
  const { port, calls } = setup({});
  await assert.rejects(port.export_report(RESULT, "pdf"), { code: "feature_unavailable" });
  const narrow = setup({}, { over: { supported_export_formats: ["json"] } });
  await assert.rejects(narrow.port.export_report(RESULT, "markdown"), { code: "feature_unavailable" });
  await assert.rejects(port.export_report("p6-result-" + "7".repeat(64), "json"), { code: "no_result" });
  assert.deepEqual([...calls, ...narrow.calls], []);
});

test("E3 a descriptor or download outside scope, hash, size, media type or expiry is refused", async () => {
  const cases = [
    // an untrustworthy answer to the create request: the export may exist, so its outcome is unknown
    [{ download_url: `/v1/workspaces/b/exports/${DURABLE}/download` }, null, "outcome_unknown"],
    [{ download_url: `https://elsewhere.example/v1/workspaces/${WS}/exports/${DURABLE}/download` }, null, "outcome_unknown"],
    [{ result_id: "p6-result-" + "8".repeat(64) }, null, "outcome_unknown"],
    [{ project_id: "p3-project-" + "2".repeat(64) }, null, "outcome_unknown"],
    [{ expires_at: null }, null, "outcome_unknown"],
    [{ expires_at: "2001-01-01T00:00:00Z" }, null, "export_expired"],
    [{ sha256: "0".repeat(64) }, download(), "response_invalid"],
    [{ byte_size: BYTES.length + 1 }, download(), "response_invalid"],
    [{}, download(BYTES, "text/html"), "response_invalid"],
    [{}, download(new Uint8Array([...BYTES, 10])), "response_invalid"],
    [{}, problem("export_expired", 410), "export_expired"],
  ];
  for (const [over, answer, code] of cases) {
    const routes = { [`POST ${B}/results/${RESULT}/exports`]: [json(descriptor(over), 201)] };
    if (answer) routes[`GET ${B}/exports/${DURABLE}/download`] = [answer];
    const { port, calls } = setup(routes);
    await assert.rejects(port.export_report(RESULT, "json"), { code }, JSON.stringify(over));
    if (!answer) assert.equal(calls.length, 1, "nothing is downloaded from a refused descriptor");
  }
  assert.equal(readProblem({ schema_version: VERSION, type: "t", title: "t", status: 410, code: "export_expired", detail: "d", request_id: "r" }, 410).code, "export_expired");
  assert.deepEqual(BINDING_PROBLEM_STATUS, { invalid_request: 400, not_found: 404, job_state_conflict: 409, export_expired: 410,
    response_too_large: 413, schema_invalid: 422, service_unavailable: 503 });
});

test("K1 the producer's binding codes are trusted only with their own status, in the header and the body", () => {
  const body = (code, status) => ({ schema_version: VERSION, type: "t", title: "t", status, code, detail: "d", request_id: "r" });
  for (const [code, status] of Object.entries(BINDING_PROBLEM_STATUS)) {
    assert.equal(readProblem(body(code, status), status).code, code);
    assert.equal(readProblem(body(code, status), status === 503 ? 500 : 503), null, `${code} with another HTTP status`);
    assert.equal(readProblem(body(code, 418), status), null, `${code} with another body status`);
  }
});

/* Kiên's review, 2026-11-11: a server that commits, then answers 5xx; a descriptor for another project;
   a succeeded job whose output or child read fails. A retry must settle the same intent, never start another. */
const committed = () => {
  const effects = new Map();
  // the first request under a key commits, then fails; a replay of that key answers what was committed
  const once = (failure, success) => (url, init) => {
    const key = init.headers["Idempotency-Key"] ?? init.headers["If-Match"];
    if (effects.has(key)) return success();
    effects.set(key, true);
    return failure();
  };
  return { effects, once };
};

test("B1 a mutation answered 500 or 503 after its effect keeps key, body and CAS, and the retry makes no second effect", async () => {
  for (const failure of [() => problem("service_unavailable", 503), () => new Response("upstream error", { status: 500, headers: { "Content-Type": "text/plain" } })]) {
    const refresh = committed(), follow = committed(), exported = committed(), deleted = committed();
    const { port, calls } = setup({
      [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
      [`POST ${B}/projects/${PROJECT}/refresh`]: [refresh.once(failure, () => json(receipt("job_r"), 202))],
      [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
      [`GET ${B}/jobs/job_r/output`]: [json({ schema_version: VERSION, workspace_id: WS, job_id: "job_r", operation: "refresh",
        result: { project_id: PROJECT, status: "unchanged", previous_snapshot_id: SNAPSHOT, snapshot_id: SNAPSHOT, revision: 4 } })],
      ...followRoutes({ revision: 5 }),
      [`POST ${B}/results/${RESULT}/follow-ups`]: [follow.once(failure, () => json(receipt("job_f"), 202))],
      [`POST ${B}/results/${RESULT}/exports`]: [exported.once(failure, () => json(descriptor(), 201))],
      [`GET ${B}/exports/${DURABLE}/download`]: [download()],
      [`DELETE ${B}/projects/${PROJECT}`]: [deleted.once(failure, () => tomb({ revision: 6 }))],
    });
    await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "outcome_unknown" });
    await port.refresh(PROJECT, { source_id: "src_new" });
    await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), { code: "outcome_unknown" });
    await port.follow_up(RESULT, "claim", CLAIM, "Why this claim?");
    await assert.rejects(port.export_report(RESULT, "json"), { code: "outcome_unknown" });
    await port.export_report(RESULT, "json");
    await assert.rejects(port.delete_project(PROJECT), { code: "outcome_unknown" });
    assert.equal((await port.delete_project(PROJECT)).revision, 6);
    for (const [path, method, effects] of [["/refresh", "POST", refresh], ["/follow-ups", "POST", follow], ["/exports", "POST", exported], [`/projects/${PROJECT}`, "DELETE", deleted]]) {
      const sent = calls.filter(call => call.method === method && call.url.endsWith(path));
      assert.equal(sent.length, 2, path);
      assert.equal(effects.effects.size, 1, `${path}: one effect`);
      for (const header of ["Idempotency-Key", "If-Match"]) assert.equal(sent[0].headers[header], sent[1].headers[header], `${path} ${header}`);
      assert.equal(sent[0].body, sent[1].body, path);
    }
    assert.deepEqual(calls.filter(call => call.method !== "GET").map(call => call.headers["If-Match"]),
      ['"3"', '"3"', '"4"', '"4"', undefined, undefined, '"5"', '"5"'], "CAS moves only with a trusted result");
  }
});

test("B2 a read answered 503 stays a read failure; only a trusted refusal below 500 settles a mutation", async () => {
  const read = setup({ [`GET ${B}/results/${CHILD}`]: [problem("service_unavailable", 503)] }, { view: childView() });
  read.port.setContext({ session, workspace: WS, caps: caps(), client: createHostedClient({ session, isCurrent: () => true, fetch: read.fetch }),
    isCurrent: () => true, initialJobId: "job_f", initialProjectId: PROJECT, initialSnapshotId: SNAPSHOT });
  await assert.rejects(read.port.view(CHILD), { code: "service_unavailable" });
  const refused = setup({
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [problem("job_state_conflict", 409), json(receipt("job_r"), 202)],
    [`GET ${B}/jobs/job_r`]: [json(done("job_r", "failed"))],
  });
  await assert.rejects(refused.port.refresh(PROJECT, { source_id: "src_new" }), { code: "job_state_conflict" });
  await assert.rejects(refused.port.refresh(PROJECT, { source_id: "src_new" }), { code: "refresh_failed" });
  const posts = refused.calls.filter(call => call.url.endsWith("/refresh"));
  assert.notEqual(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"], "a settled refusal ends the intent");
});

test("A1 a descriptor for another project is unsettled: nothing is downloaded and the retry replays the same key", async () => {
  const { port, calls } = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [json(descriptor({ project_id: "p3-project-" + "2".repeat(64) }), 201), json(descriptor(), 201)],
    [`GET ${B}/exports/${DURABLE}/download`]: [problem("service_unavailable", 503), download()],
  });
  await assert.rejects(port.export_report(RESULT, "json"), { code: "outcome_unknown" });
  assert.equal(calls.length, 1, "no download from an untrusted descriptor");
  // a failed download keeps the intent too; only an expired or missing export ends it
  await assert.rejects(port.export_report(RESULT, "json"), { code: "service_unavailable" });
  assert.deepEqual(await port.export_report(RESULT, "json"), BYTES);
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 3);
  assert.equal(new Set(posts.map(call => call.headers["Idempotency-Key"])).size, 1);
  const fresh = setup({ [`POST ${B}/results/${RESULT}/exports`]: [json(descriptor(), 201)], [`GET ${B}/exports/${DURABLE}/download`]: [download()] });
  await fresh.port.export_report(RESULT, "json"); await fresh.port.export_report(RESULT, "json");
  const twice = fresh.calls.filter(call => call.method === "POST");
  assert.notEqual(twice[0].headers["Idempotency-Key"], twice[1].headers["Idempotency-Key"], "a delivered export ends its intent");
});

test("C1 a refresh whose output read fails resumes the same job: same upload, no second POST, key or CAS", async () => {
  const zip = new File(["PK synthetic"], "replacement.zip", { type: "application/zip", lastModified: 1 });
  const { port, calls } = setup({
    [`POST ${B}/sources`]: [json({ schema_version: VERSION, workspace_id: WS, source_id: "src_new", sha256: "b".repeat(64) }, 201)],
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [json(receipt("job_r"), 202)],
    [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
    [`GET ${B}/jobs/job_r/output`]: [problem("service_unavailable", 503), json({ schema_version: VERSION, workspace_id: WS, job_id: "job_r",
      operation: "refresh", result: { project_id: PROJECT, status: "new_snapshot", previous_snapshot_id: SNAPSHOT, snapshot_id: NEW_SNAPSHOT, revision: 4 } })],
  });
  await assert.rejects(port.refresh(PROJECT, await port.uploadReplacement(PROJECT, zip)), { code: "service_unavailable" });
  // the person chooses the same ZIP again, as the hosted Refresh button asks: a new File, renamed and re-dated
  const again = new File(["PK synthetic"], "copy of replacement.zip", { type: "application/zip", lastModified: 2 });
  const outcome = await port.refresh(PROJECT, await port.uploadReplacement(PROJECT, again));
  assert.equal(outcome.snapshot_id, NEW_SNAPSHOT);
  assert.equal(calls.filter(call => call.url.endsWith("/sources")).length, 1, "the uploaded source is reused");
  assert.equal(calls.filter(call => call.url.endsWith("/refresh")).length, 1, "the succeeded job is read again, not resubmitted");
  assert.equal(calls.filter(call => call.url === `${B}/projects/${PROJECT}`).length, 1);
  assert.equal(calls.filter(call => call.url === `${B}/jobs/job_r`).length, 2);
  // settled: the same file now starts a new upload and a new refresh intent
  await port.uploadReplacement(PROJECT, zip);
  assert.equal(calls.filter(call => call.url.endsWith("/sources")).length, 2);
});

/* Kiên's repro (Phan-hoi-Quan-recovery-20261111): two ZIPs with the same name, type, size and date. */
const META = { type: "application/zip", lastModified: 1 }, ZIP_A = "PK synthetic A", ZIP_B = "PK synthetic B";

test("C3 a different ZIP with the same name, type, size and date is a new upload, never the held source or job", async () => {
  const { port, calls } = setup({
    [`POST ${B}/sources`]: [json({ schema_version: VERSION, workspace_id: WS, source_id: "src_a", sha256: sha(ZIP_A) }, 201),
      json({ schema_version: VERSION, workspace_id: WS, source_id: "src_b", sha256: sha(ZIP_B) }, 201)],
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [json(receipt("job_r"), 202), problem("revision_conflict", 412)],
    [`GET ${B}/jobs/job_r`]: [json(done("job_r"))],
    [`GET ${B}/jobs/job_r/output`]: [problem("service_unavailable", 503)],
  });
  const a = new File([ZIP_A], "replacement.zip", META), b = new File([ZIP_B], "replacement.zip", META);
  await assert.rejects(port.refresh(PROJECT, await port.uploadReplacement(PROJECT, a)), { code: "service_unavailable" });
  const source = await port.uploadReplacement(PROJECT, b);
  assert.deepEqual(source, { source_id: "src_b" });
  const uploads = calls.filter(call => call.url.endsWith("/sources"));
  assert.equal(sha(Buffer.from(await uploads[1].body.arrayBuffer())), sha(ZIP_B), "B's own bytes are sent");
  assert.notEqual(uploads[0].headers["Idempotency-Key"], uploads[1].headers["Idempotency-Key"]);
  // job A may already have committed, so B's refresh is its own intent under the CAS read before and the server decides
  await assert.rejects(port.refresh(PROJECT, source), { code: "revision_conflict" });
  const posts = calls.filter(call => call.url.endsWith("/refresh"));
  assert.deepEqual(posts.map(call => JSON.parse(call.body).source_id), ["src_a", "src_b"]);
  assert.deepEqual(posts.map(call => call.headers["If-Match"]), ['"3"', '"3"']);
  assert.notEqual(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"]);
  assert.equal(calls.filter(call => call.url.endsWith("/output")).length, 1, "job A's output is never reported as B's refresh");
});

test("C4 a lost part answer resumes only the same content; a different ZIP with the same metadata starts its own upload", async () => {
  const state = (upload, content, received, ready = false, status = 200) => json({ schema_version: VERSION, workspace_id: WS, upload_id: upload,
    state: ready ? "ready" : "active", byte_size: content.length, sha256: sha(content), expires_at: "2099-01-01T00:00:00Z",
    received_bytes: received ? content.length : 0, received_parts: received ? [1] : [], missing_parts: received ? [] : [1],
    source_id: ready ? "src_" + upload : null }, status);
  const lost = () => { throw new TypeError("synthetic loss of the accepted part's answer"); };
  const resumable = { over: { features: { ...FEATURES, "sources.resumable": true } } };
  const routes = () => ({
    [`POST ${B}/uploads`]: [state("upload_a", ZIP_A, false, false, 201), state("upload_b", ZIP_B, false, false, 201)],
    [`PUT ${B}/uploads/upload_a/parts/1`]: [lost],
    [`GET ${B}/uploads/upload_a`]: [state("upload_a", ZIP_A, true)],
    [`POST ${B}/uploads/upload_a/complete`]: [state("upload_a", ZIP_A, true, true)],
    [`PUT ${B}/uploads/upload_b/parts/1`]: [state("upload_b", ZIP_B, true)],
    [`POST ${B}/uploads/upload_b/complete`]: [state("upload_b", ZIP_B, true, true)],
  });
  const starts = calls => calls.filter(call => call.method === "POST" && call.url === `${B}/uploads`);

  const same = setup(routes(), resumable);
  await assert.rejects(same.port.uploadReplacement(PROJECT, new File([ZIP_A], "replacement.zip", META)), { code: "outcome_unknown" });
  assert.deepEqual(await same.port.uploadReplacement(PROJECT, new File([ZIP_A], "renamed.zip", { type: "application/zip", lastModified: 2 })),
    { source_id: "src_upload_a" }, "the same bytes in a new File resume upload A");
  assert.equal(starts(same.calls).length, 1);
  assert.equal(same.calls.filter(call => call.url === `${B}/uploads/upload_a`).length, 1);

  const other = setup(routes(), resumable);
  await assert.rejects(other.port.uploadReplacement(PROJECT, new File([ZIP_A], "replacement.zip", META)), { code: "outcome_unknown" });
  assert.deepEqual(await other.port.uploadReplacement(PROJECT, new File([ZIP_B], "replacement.zip", META)), { source_id: "src_upload_b" });
  const created = starts(other.calls);
  assert.deepEqual(created.map(call => JSON.parse(call.body).sha256), [sha(ZIP_A), sha(ZIP_B)]);
  assert.notEqual(created[0].headers["Idempotency-Key"], created[1].headers["Idempotency-Key"]);
  assert.equal(other.calls.filter(call => call.url === `${B}/uploads/upload_a`).length, 0, "B never reads upload A's status");
});

test("C5 an empty, oversized or abandoned replacement is refused before any request", async () => {
  const { port, calls } = setup({});
  await assert.rejects(port.uploadReplacement(PROJECT, new Blob([])), { code: "invalid_request" });
  await assert.rejects(port.uploadReplacement(PROJECT, new Blob([new Uint8Array(caps().upload_bundle_bytes + 1)])), { code: "invalid_request" });
  const stop = new AbortController(); stop.abort();
  await assert.rejects(port.uploadReplacement(PROJECT, new Blob([ZIP_A]), stop.signal), HostedRequestDiscarded);
  assert.deepEqual(calls, []);
});

test("C2 a follow-up whose child read fails resumes the same job, keeps the first CAS and moves it only after the child is trusted", async () => {
  const routes = followRoutes();
  routes[`GET ${B}/results/${CHILD}`] = [problem("service_unavailable", 503),
    json({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, result_id: CHILD, view: childView() })];
  routes[`DELETE ${B}/projects/${PROJECT}`] = [problem("revision_conflict", 412)];
  const { port, calls } = setup(routes);
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), { code: "service_unavailable" });
  assert.equal((await port.demo_view()).result.id, RESULT, "the parent stays open");
  await assert.rejects(port.delete_project(PROJECT), { code: "revision_conflict" });
  assert.equal(calls.at(-1).headers["If-Match"], '"3"', "an unread child does not raise the CAS");
  // the CAS 412 dropped the cached revision; the held follow-up intent still keeps its own
  const child = await port.follow_up(RESULT, "claim", CLAIM, "Why this claim?");
  assert.equal(child.result.id, CHILD);
  assert.equal(calls.filter(call => call.url.endsWith("/follow-ups")).length, 1, "no second follow-up job or child");
  assert.equal(calls.filter(call => call.url === `${B}/projects/${PROJECT}` && call.method === "GET").length, 1);
  routes[`DELETE ${B}/projects/${PROJECT}`] = [tomb({ revision: 5 })];
  await port.delete_project(PROJECT);
  assert.equal(calls.at(-1).headers["If-Match"], '"4"', "the trusted child moved the CAS to its revision");
});

test("E4 a revoke between the descriptor and the download stops before any bytes are read", async () => {
  let current = true;
  const { port, calls } = setup({
    [`POST ${B}/results/${RESULT}/exports`]: [() => { current = false; return json(descriptor(), 201); }],
    [`GET ${B}/exports/${DURABLE}/download`]: [download()],
  }, { isCurrent: () => current });
  await assert.rejects(port.export_report(RESULT, "json"), HostedRequestDiscarded);
  assert.equal(calls.length, 1);
});

const tomb = (over = {}) => json({ schema_version: VERSION, workspace_id: WS, project_id: PROJECT, revision: 4, tombstone_id: TOMBSTONE,
  status: "tombstoned", retention: "retained", ...over });

test("D1 delete sends {} with If-Match and returns a typed tombstone, with no counts made up", async () => {
  const { port, calls } = setup({ [`GET ${B}/projects/${PROJECT}`]: [json(details(3))], [`DELETE ${B}/projects/${PROJECT}`]: [tomb()] });
  const receipt = await port.delete_project(PROJECT);
  assert.deepEqual(receipt, { project_id: PROJECT, status: "tombstoned", retention: "retained", revision: 4, tombstone_id: TOMBSTONE });
  assert.ok(isTombstone(receipt));
  assert.ok(!("deleted_count" in receipt) && !("retained_shared_count" in receipt));
  const del = calls.find(call => call.method === "DELETE");
  assert.equal(del.body, "{}"); assert.equal(del.headers["If-Match"], '"3"');
  assert.equal(del.headers["Idempotency-Key"], undefined, "a plain CAS receipt: no key is invented");
  await assert.rejects(port.demo_view(), { code: "no_result" }, "the deleted project's result is closed in the port");
});

test("D2 a stale delete is a conflict to review; a lost answer retries the same If-Match, which the server replays", async () => {
  const stale = setup({ [`GET ${B}/projects/${PROJECT}`]: [json(details(3)), json(details(6))],
    [`DELETE ${B}/projects/${PROJECT}`]: [problem("revision_conflict", 412), tomb({ revision: 7 })] });
  await assert.rejects(stale.port.delete_project(PROJECT), { code: "revision_conflict" });
  assert.equal((await stale.port.delete_project(PROJECT)).revision, 7);
  assert.deepEqual(stale.calls.filter(call => call.method === "DELETE").map(call => call.headers["If-Match"]), ['"3"', '"6"']);
  let first = true;
  const lost = setup({ [`GET ${B}/projects/${PROJECT}`]: [json(details(3))],
    [`DELETE ${B}/projects/${PROJECT}`]: [() => { if (first) { first = false; throw new TypeError("reset"); } return tomb(); }] });
  await assert.rejects(lost.port.delete_project(PROJECT), { code: "outcome_unknown" });
  assert.equal((await lost.port.delete_project(PROJECT)).tombstone_id, TOMBSTONE);
  assert.deepEqual(lost.calls.filter(call => call.method === "DELETE").map(call => call.headers["If-Match"]), ['"3"', '"3"']);
  assert.equal(lost.calls.filter(call => call.method === "GET").length, 1);
});

test("D3 a receipt for another project, a skipped revision or a purge claim is not accepted as deleted", async () => {
  for (const over of [{ project_id: "p3-project-" + "3".repeat(64) }, { revision: 5 }, { status: "purged" }, { retention: "removed" }, { tombstone_id: "x" }]) {
    const { port } = setup({ [`GET ${B}/projects/${PROJECT}`]: [json(details(3))], [`DELETE ${B}/projects/${PROJECT}`]: [tomb(over)] });
    // the server took a change it described untrustworthily, so the outcome is unknown, never "deleted"
    await assert.rejects(port.delete_project(PROJECT), { code: "outcome_unknown" }, JSON.stringify(over));
  }
  const missing = setup({ [`GET ${B}/projects/${PROJECT}`]: [problem("not_found", 404)] });
  await assert.rejects(missing.port.delete_project(PROJECT), { code: "not_found" });
});

const conflictCaps = { features: { ...FEATURES, "jobs.execution_v1": true, "jobs.conflict_v1": true } };
const conflictHistory = (project = PROJECT) => ({ schema_version: VERSION, workspace_id: WS, job_id: "job_c", retry_of_job_id: null,
  items: [{ epoch: 1, started_at: "2026-11-14T08:00:00Z", heartbeat_at: null, lease_until: null, finished_at: "2026-11-14T08:00:02Z",
    outcome: "failed", failure_kind: "conflict" }], conflict: { project_id: project, current_revision: 5 } });

test("F5 jobs.conflict_v1: a follow-up that met a changed project says so, retries nothing, and the next ask reads a fresh revision and key", async () => {
  const routes = { ...followRoutes(),
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3)), json(details(5))],
    [`POST ${B}/results/${RESULT}/follow-ups`]: [json(receipt("job_c"), 202), json(receipt("job_f"), 202)],
    [`GET ${B}/jobs/job_c`]: [json(done("job_c", "failed"))],
    [`GET ${B}/jobs/job_c/attempts`]: [json(conflictHistory())] };
  const { port, calls } = setup(routes, { over: conflictCaps });
  await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), { code: "project_changed" });
  const posts = () => calls.filter(call => call.url.endsWith("/follow-ups"));
  assert.equal(posts().length, 1, "nothing is retried by itself");
  assert.equal(calls.some(call => call.url.endsWith("/retry")), false);
  const child = await port.follow_up(RESULT, "claim", CLAIM, "Why this claim?");
  assert.equal(child.result.id, CHILD);
  assert.equal(calls.filter(call => call.url === `${B}/projects/${PROJECT}`).length, 2, "the revision is read again after the conflict");
  assert.equal(posts()[1].headers["If-Match"], '"5"');
  assert.notEqual(posts()[1].headers["Idempotency-Key"], posts()[0].headers["Idempotency-Key"], "a new explicit ask has its own key");
});

test("F6 without jobs.conflict_v1, or when the history names another project, the job's own failure stands", async () => {
  for (const [over, history, reads] of [[{}, conflictHistory(), 0], [conflictCaps, conflictHistory("p3-project-" + "9".repeat(64)), 1],
    [conflictCaps, { ...conflictHistory(), items: [{ ...conflictHistory().items[0], failure_kind: "permanent" }], conflict: undefined }, 1]]) {
    const routes = { ...followRoutes(), [`POST ${B}/results/${RESULT}/follow-ups`]: [json(receipt("job_c"), 202)],
      [`GET ${B}/jobs/job_c`]: [json(done("job_c", "failed"))], [`GET ${B}/jobs/job_c/attempts`]: [json(history)] };
    const { port, calls } = setup(routes, { over });
    await assert.rejects(port.follow_up(RESULT, "claim", CLAIM, "Why this claim?"), { code: "follow_up_failed" });
    assert.equal(calls.filter(call => call.url.endsWith("/attempts")).length, reads);
  }
});

test("R7 jobs.conflict_v1: a refresh that met a newer project is a stale refresh; nothing is retried and the next one reads a fresh revision", async () => {
  const output = { schema_version: VERSION, workspace_id: WS, job_id: "job_r2", operation: "refresh",
    result: { project_id: PROJECT, status: "new_snapshot", previous_snapshot_id: SNAPSHOT, snapshot_id: NEW_SNAPSHOT, revision: 6 } };
  const { port, calls } = setup({
    [`GET ${B}/projects/${PROJECT}`]: [json(details(3)), json(details(5))],
    [`POST ${B}/projects/${PROJECT}/refresh`]: [json(receipt("job_c"), 202), json(receipt("job_r2"), 202)],
    [`GET ${B}/jobs/job_c`]: [json(done("job_c", "failed"))],
    [`GET ${B}/jobs/job_c/attempts`]: [json(conflictHistory())],
    [`GET ${B}/jobs/job_r2`]: [json(done("job_r2"))],
    [`GET ${B}/jobs/job_r2/output`]: [json(output)],
  }, { over: conflictCaps });
  await assert.rejects(port.refresh(PROJECT, { source_id: "src_new" }), { code: "refresh_stale" });
  assert.equal(calls.some(call => call.url.endsWith("/retry")), false);
  await port.refresh(PROJECT, { source_id: "src_new" });
  const posts = calls.filter(call => call.url.endsWith("/refresh"));
  assert.deepEqual(posts.map(call => call.headers["If-Match"]), ['"3"', '"5"']);
  assert.notEqual(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"]);
});
