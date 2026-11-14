import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createHostedClient } from "../src/service/hosted-client.ts";
import { readProblem, UPLOAD_PROBLEM_STATUS } from "../src/service/hosted.ts";

const VERSION = "product-api/v1-draft", KEY = "synthetic-resumable-key-0001", PART = 4 * 1024 * 1024;
const session = { user_id: "actor", context_id: "ctx", csrf_token: "synthetic-csrf" };
const caps = { workspace_id: "a", allowed_actions: ["import", "analyze"], upload_bundle_bytes: 200 * 1024 * 1024,
  question_chars: 2000, job_poll_min_ms: 10, features: { "sources.resumable": true, "sources.preview_lease": true } };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const problem = (code, status) => ({ schema_version: VERSION, type: "urn:test:upload", title: "Safe refusal", code,
  status, detail: "Safe synthetic refusal.", request_id: "synthetic_request" });
function staging(bytes, { losePart = false, loseComplete = false, loseCreate = false, patch = value => value,
  beforePartResponse, onAnswer } = {}) {
  const slices = []; for (let offset = 0; offset < bytes.length; offset += PART) slices.push(bytes.subarray(offset, Math.min(offset + PART, bytes.length)));
  // Independent Node oracle: each expected slice is hashed separately, never .subarray().buffer.
  const expected = { schema_version: VERSION, source_type: "zip_bundle", byte_size: bytes.length, sha256: digest(bytes),
    parts: slices.map((slice, i) => ({ part_number: i + 1, byte_size: slice.length, sha256: digest(slice) })), parent_source_id: null };
  const received = new Set(), calls = []; let state = "active";
  const status = () => patch({ schema_version: VERSION, workspace_id: "a", upload_id: "upload1", state,
    byte_size: bytes.length, sha256: expected.sha256, expires_at: "2030-01-01T00:00:00Z",
    received_bytes: [...received].reduce((sum, number) => sum + slices[number - 1].length, 0),
    received_parts: [...received], missing_parts: slices.map((_, i) => i + 1).filter(n => !received.has(n)),
    source_id: state === "ready" ? "source1" : null });
  const client = createHostedClient({ session, isCurrent: () => true, onAnswer, fetch: async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/uploads")) {
      assert.equal(init.method, "POST"); assert.deepEqual(JSON.parse(init.body), expected);
      assert.equal(init.headers["Idempotency-Key"], KEY);
      if (loseCreate) { loseCreate = false; throw new TypeError("Synthetic lost create response"); }
      return json(status(), 201);
    }
    if (url.endsWith("/uploads/upload1")) { assert.equal(init.method, "GET"); return json(status()); }
    if (url.endsWith("/complete")) {
      assert.equal(init.method, "POST"); assert.equal(init.body, "{}");
      assert.equal(received.size, slices.length); state = "ready";
      if (loseComplete) { loseComplete = false; throw new TypeError("Synthetic lost complete response"); }
      return json(status());
    }
    const number = Number(url.split("/").at(-1));
    assert.ok(url.endsWith("/parts/" + number)); assert.equal(init.method, "PUT");
    assert.equal(init.headers["Content-Type"], "application/octet-stream");
    const payload = Buffer.from(await init.body.arrayBuffer());
    assert.deepEqual(payload, slices[number - 1]); assert.equal(digest(payload), expected.parts[number - 1].sha256);
    received.add(number);
    if (losePart) { losePart = false; throw new TypeError("Synthetic lost part response"); }
    await beforePartResponse?.(number);
    return json(status());
  } });
  return { client, calls, expected };
}

test("multipart hash/body oracle and retry skip a server-acknowledged part without a new upload", async () => {
  const bytes = Buffer.concat([Buffer.alloc(PART, 0x61), Buffer.from("distinct final bytes")]);
  const h = staging(bytes, { losePart: true }), resume = {}, progress = [];
  const blob = new Blob([bytes]);
  await assert.rejects(h.client.uploadZip("a", caps, blob, KEY, { resume }), { code: "outcome_unknown" });
  assert.equal(resume.upload_id, "upload1"); assert.equal(h.calls.length, 2, "no automatic mutation retry");
  const source = await h.client.uploadZip("a", caps, blob, KEY, { resume, onProgress: status => progress.push(status.received_bytes) });
  assert.deepEqual(source, { workspace_id: "a", source_id: "source1", sha256: digest(bytes) });
  assert.equal(h.calls.filter(call => call.url.endsWith("/uploads")).length, 1);
  assert.equal(h.calls.filter(call => call.url.endsWith("/parts/1")).length, 1);
  assert.equal(h.calls.filter(call => call.url.endsWith("/parts/2")).length, 1);
  assert.equal(h.calls[2].url, "/v1/workspaces/a/uploads/upload1");
  assert.equal(progress[0], PART); assert.equal(progress.at(-1), bytes.length);
  for (const { init } of h.calls.filter(call => call.init.method !== "GET")) {
    assert.equal(init.headers["X-CSRF-Token"], session.csrf_token); assert.equal(init.credentials, "same-origin");
  }
});

test("a lost create keeps its original key; a lost complete recovers ready source through GET", async () => {
  const bytes = Buffer.from("small synthetic ZIP");
  for (const lost of ["loseCreate", "loseComplete"]) {
    const h = staging(bytes, { [lost]: true }), resume = {}, blob = new Blob([bytes]);
    await assert.rejects(h.client.uploadZip("a", caps, blob, KEY, { resume }), { code: "outcome_unknown" });
    const source = await h.client.uploadZip("a", caps, blob, KEY, { resume });
    assert.equal(source.source_id, "source1");
    assert.equal(h.calls.filter(call => call.url.endsWith("/parts/1")).length, 1);
    assert.equal(h.calls.filter(call => call.url.endsWith("/complete")).length, 1);
    if (lost === "loseCreate") assert.equal(h.calls.filter(call => call.url.endsWith("/uploads")).length, 2);
    else assert.equal(h.calls.at(-1).url, "/v1/workspaces/a/uploads/upload1");
  }
});

test("substituted upload scope/content/progress is refused before sending a part", async () => {
  const patches = [value => ({ ...value, workspace_id: "foreign" }), value => ({ ...value, sha256: "b".repeat(64) }),
    value => ({ ...value, byte_size: value.byte_size + 1 }), value => ({ ...value, source_id: "premature" }),
    value => ({ ...value, missing_parts: [1, 1] }), value => ({ ...value, received_bytes: 1 }),
    value => ({ ...value, expires_at: "2030-02-30T00:00:00Z" })];
  for (const patch of patches) {
    const h = staging(Buffer.from("zip"), { patch });
    await assert.rejects(h.client.uploadZip("a", caps, new Blob(["zip"]), KEY));
    assert.equal(h.calls.length, 1);
  }
  const h = staging(Buffer.from("zip"), { patch: value => ({ ...value, upload_id: "another" }) });
  await assert.rejects(h.client.uploadZip("a", caps, new Blob(["zip"]), KEY, { resume: { upload_id: "upload1" } }), { code: "response_invalid" });
  assert.equal(h.calls[0].init.method, "GET");
});

test("expired/cancelled uploads stop; a published ready source survives its staging expiry", async () => {
  for (const patch of [value => ({ ...value, state: "expired" }), value => ({ ...value, expires_at: "2000-01-01T00:00:00Z" }),
    value => ({ ...value, state: "cancelled" })]) {
    const h = staging(Buffer.from("zip"), { patch });
    await assert.rejects(h.client.uploadZip("a", caps, new Blob(["zip"]), KEY), error => ["upload_expired", "upload_cancelled"].includes(error.code));
    assert.equal(h.calls.length, 1);
  }
  const h = staging(Buffer.from("zip"), { patch: value => ({ ...value, state: "ready", expires_at: "2000-01-01T00:00:00Z",
    received_parts: [1], missing_parts: [], received_bytes: 3, source_id: "source1" }) });
  assert.equal((await h.client.uploadZip("a", caps, new Blob(["zip"]), KEY)).source_id, "source1");
  assert.equal(h.calls.length, 1);
});

test("a context change while reading the file prevents every upload request", async () => {
  let current = true, resolve, calls = 0;
  class DelayedBlob extends Blob { arrayBuffer() { return new Promise(done => { resolve = done; }); } }
  const client = createHostedClient({ session, isCurrent: () => current, fetch: async () => { calls++; throw new Error("must not send"); } });
  const pending = client.uploadZip("a", caps, new DelayedBlob(["zip"]), KEY);
  current = false; resolve(new TextEncoder().encode("zip").buffer);
  await assert.rejects(pending, { name: "HostedRequestDiscarded" }); assert.equal(calls, 0);
});

test("abandoning the form during file reading or hashing prevents upload creation in the same session", async () => {
  for (const phase of ["read", "hash"]) {
    let release, entered;
    const started = new Promise(resolve => { entered = resolve; });
    const held = new Promise(resolve => { release = resolve; });
    const originalCrypto = globalThis.crypto;
    class DelayedBlob extends Blob { async arrayBuffer() { entered(); await held; return super.arrayBuffer(); } }
    if (phase === "hash") Object.defineProperty(globalThis, "crypto", { configurable: true, value: { subtle: {
      digest: async (...args) => { entered(); await held; return originalCrypto.subtle.digest(...args); },
    } } });
    try {
      let calls = 0;
      const controller = new AbortController();
      const client = createHostedClient({ session, isCurrent: () => true, fetch: async () => { calls++; throw new Error("must not send"); } });
      const pending = client.uploadZip("a", caps, phase === "read" ? new DelayedBlob(["zip"]) : new Blob(["zip"]), KEY, { signal: controller.signal });
      await started; controller.abort(); release();
      await assert.rejects(pending, error => error.name === "HostedRequestDiscarded" && !error.outcome_unknown);
      assert.equal(calls, 0, phase + " must not create a late upload");
    } finally { Object.defineProperty(globalThis, "crypto", { configurable: true, value: originalCrypto }); }
  }
});

test("abandoning an accepted held part preserves uncertain outcome and stops all subsequent mutations", async () => {
  let release, entered;
  const started = new Promise(resolve => { entered = resolve; });
  const held = new Promise(resolve => { release = resolve; });
  const answers = [], progress = [], controller = new AbortController();
  const bytes = Buffer.concat([Buffer.alloc(PART, 0x61), Buffer.from("second part")]);
  const h = staging(bytes, {
    beforePartResponse: async number => { assert.equal(number, 1); entered(); await held; },
    onAnswer: (...answer) => answers.push(answer),
  });
  const resume = {};
  const pending = h.client.uploadZip("a", caps, new Blob([bytes]), KEY,
    { resume, signal: controller.signal, onProgress: status => progress.push(status.received_bytes) });
  // Use the oracle's exact body; the accepted PUT has not returned to the client.
  await started; controller.abort();
  await assert.rejects(pending, error => error.name === "HostedRequestDiscarded" && error.outcome_unknown);
  release(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(resume.upload_id, "upload1", "the known durable upload identity remains available for reconciliation");
  assert.deepEqual(h.calls.map(call => call.init.method), ["POST", "PUT"]);
  assert.deepEqual(progress, [0], "late accepted response must not publish progress");
  assert.equal(answers.length, 1, "late accepted response must not renew authority");
});

test("false or absent flags retain the exact legacy upload and job envelopes", async () => {
  for (const features of [undefined, { "sources.resumable": false, "sources.preview_lease": false }]) {
    const calls = [];
    const client = createHostedClient({ session, isCurrent: () => true, fetch: async (url, init) => {
      calls.push({ url, init }); return url.endsWith("/sources")
        ? json({ schema_version: VERSION, workspace_id: "a", source_id: "source1", sha256: "a".repeat(64) }, 201)
        : json(receipt, 202);
    } });
    const old = { ...caps, features };
    await client.uploadZip("a", old, new Blob(["zip"]), KEY);
    const submission = { operation: "confirm", source_id: "source1", mapping: { target: {} } };
    await client.submitJob("a", old, submission, KEY);
    assert.equal(calls[0].url, "/v1/workspaces/a/sources"); assert.equal(calls[0].init.headers["Content-Type"], "application/zip");
    assert.equal(calls[1].url, "/v1/workspaces/a/jobs"); assert.deepEqual(JSON.parse(calls[1].init.body), submission);
  }
});

const fixtureRoot = new URL("../../../fixtures/metric-dimensions-hosted-v2/", import.meta.url);
const maintainedPreview = JSON.parse(readFileSync(new URL("preview.json", fixtureRoot), "utf8"));
const mapping = JSON.parse(readFileSync(new URL("confirm.json", fixtureRoot), "utf8")).mapping;
const receipt = { schema_version: VERSION, workspace_id: "a", job_id: "job1", request_id: "request1", status: "queued", status_url: "/v1/workspaces/a/jobs/job1" };
test("leased import uses exact producer metadata and scoped routes; no client underscore binding", async () => {
  const calls = [];
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async (url, init) => {
    calls.push({ url, init }); return json(url.endsWith("/output") ? { schema_version: VERSION, workspace_id: "a", job_id: "job1", operation: "preview", result: maintainedPreview } : receipt, url.endsWith("/output") ? 200 : 202);
  } });
  await client.submitJob("a", caps, { operation: "preview", source_id: maintainedPreview.source_id }, KEY);
  assert.equal(calls[0].url, "/v1/workspaces/a/imports/preview");
  assert.deepEqual(JSON.parse(calls[0].init.body), { schema_version: VERSION, source_id: maintainedPreview.source_id });
  const output = await client.loadOutput("a", "job1", { operation: "preview", sourceSha256: maintainedPreview.source_sha256, leaseSourceId: maintainedPreview.source_id });
  assert.equal(output.result.preview_id, maintainedPreview.preview_id); assert.equal(output.result.expires_at, maintainedPreview.expires_at);
  await client.submitJob("a", caps, { operation: "confirm", source_id: maintainedPreview.source_id, preview_id: output.result.preview_id, mapping }, KEY);
  assert.equal(calls[2].url, `/v1/workspaces/a/imports/${maintainedPreview.preview_id}/confirm`);
  assert.deepEqual(JSON.parse(calls[2].init.body), { schema_version: VERSION, mapping });
  assert.throws(() => client.submitJob("a", caps, { operation: "confirm", source_id: maintainedPreview.source_id, mapping }, KEY), { code: "invalid_request" });
  assert.throws(() => client.submitJob("a", caps, { operation: "confirm", source_id: maintainedPreview.source_id, preview_id: maintainedPreview.preview_id, mapping: { ...mapping, _preview_id: maintainedPreview.preview_id } }, KEY), { code: "invalid_request" });
});

test("leased preview cannot substitute source/workspace/lease or omit its expiry", async () => {
  for (const patch of [{ workspace_id: "other" }, { source_id: "other" }, { preview_id: "bad" }, { expires_at: undefined },
    { expires_at: "2030-02-30T00:00:00Z" }, { schema_version: "product-api/v99" }]) {
    const client = createHostedClient({ session, isCurrent: () => true, fetch: async () => json({ schema_version: VERSION,
      workspace_id: "a", job_id: "job1", operation: "preview", result: { ...maintainedPreview, ...patch } }) });
    await assert.rejects(client.loadOutput("a", "job1", { operation: "preview", sourceSha256: maintainedPreview.source_sha256, leaseSourceId: maintainedPreview.source_id }));
  }
});

test("lease refusal codes are status-bound; retry of the same accepted confirm is not locally expiry-blocked", async () => {
  for (const [code, status] of Object.entries(UPLOAD_PROBLEM_STATUS)) {
    assert.equal(readProblem(problem(code, status), status).code, code);
    assert.equal(readProblem(problem(code, status), status === 409 ? 410 : 409), null);
  }
  let sent = 0;
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async (_url, init) => {
    assert.equal(init.headers["Idempotency-Key"], KEY); sent++;
    // The producer accepted once; the original key recovers its receipt after the lease elapsed.
    if (sent === 1) throw new TypeError("Synthetic lost accepted confirm response");
    return json(receipt, 202);
  } });
  const submission = { operation: "confirm", source_id: maintainedPreview.source_id, preview_id: maintainedPreview.preview_id, mapping };
  await assert.rejects(client.submitJob("a", caps, submission, KEY), { code: "outcome_unknown" });
  assert.equal((await client.submitJob("a", caps, submission, KEY)).job_id, "job1");
  assert.equal(sent, 2);
});
