/* Consumer side of J-01 (producer review v5): Kiên's producer fixtures, unchanged, through the client's
   own boundary code. tests/fixtures/j01-review-v5/ is a copy of fixtures/j01/producer-review-v5/ from
   j01-producer-review-v5.zip (2026-11-08). Each negative case starts from a producer fixture and breaks one thing.
   Run: npm test
   Against another producer drop, unchanged file names: J01_FIXTURES=/path/to/producer-fixtures npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { DETAIL_SHOWN, featureOn, PROBLEM_STATUS, readCapabilities, readJob, readJobReceipt, readPage, readProblem, readSession } from "../src/service/hosted.ts";
import { LiveProductPort } from "../src/service/live.ts";
import { createSessionGuard } from "../src/service/session.ts";
import { QUESTION_MAX } from "../src/service/question.ts";
import { localCapabilities } from "../src/service/capabilities.ts";

const DIR = process.env.J01_FIXTURES || join(dirname(fileURLToPath(import.meta.url)), "fixtures/j01-review-v5");
const fixture = f => JSON.parse(readFileSync(join(DIR, f), "utf8"));
const PROBLEMS = ["session-expired", "session-revoked", "membership-revoked", "permission-denied", "csrf-invalid"]
  .map(n => fixture(`problem-${n}.json`));
const LATER = ["cursor-invalid", "cursor-expired", "idempotency-conflict", "revision-conflict", "precondition-required"].map(n => fixture(`problem-${n}.json`));
const coded = code => e => e.name === "ProductError" && e.code === code;
const reply = (status, body) => ({ ok: status < 300, status, json: async () => body, arrayBuffer: async () => new ArrayBuffer(0) });

/** One request answered with `body` at `httpStatus`, seen by a guard whose session check says `ok`. */
async function through(body, httpStatus) {
  let wipes = 0;
  const checks = [];
  const guard = createSessionGuard({
    contextId: "ctx_synthetic_001", now: Date.now, onWipe: () => { wipes += 1; },
    check: async () => { checks.push(1); return { status: "ok", context_id: "ctx_synthetic_001" }; },
  });
  let during;
  const port = new LiveProductPort({ fetch: async () => reply(httpStatus, body), onAnswer: (s, c) => { guard.noteResponse(s, c); during = guard.state(); }, delay: async () => {} });
  const error = await port.refresh("p").then(() => null, e => e);
  await new Promise(r => setTimeout(r, 0));
  return { error, during, after: guard.state(), wipes, checks: checks.length };
}

test("J1 the five producer Problems: each code is read, and only revoked ends access", async () => {
  const want = {
    session_expired: { state: "expired", wipes: 0 },
    session_revoked: { state: "revoked", wipes: 1 },
    membership_revoked: { state: "revoked", wipes: 1 },
    permission_denied: { state: "active", wipes: 0 },
    csrf_invalid: { state: "active", wipes: 0 },
  };
  for (const p of PROBLEMS) {
    const r = await through(p, p.status);
    assert.equal(r.error.code, p.code, p.code);
    assert.equal(r.error.safe_message, p.detail, `${p.code}: the service's detail is what is shown`);
    assert.equal(r.after, want[p.code].state, p.code);
    assert.equal(r.wipes, want[p.code].wipes, p.code);
  }
});

test("J2 a code with the wrong status is not believed: covered, checked, never wiped or expired", async () => {
  for (const p of PROBLEMS) {
    for (const http of [401, 403]) {
      if (http === p.status) continue;
      // both sides agree with each other but not with the table
      const r1 = await through({ ...p, status: http }, http);
      // the body says one status, the response another
      const r2 = await through(p, http);
      for (const r of [r1, r2]) {
        assert.equal(r.error.code, "access_unconfirmed", `${p.code} at ${http}`);
        assert.equal(r.during, "confirming", "content is covered until the session answers");
        assert.equal(r.checks, 1);
        assert.equal(r.after, "active");
        assert.equal(r.wipes, 0);
      }
    }
  }
});

test("J3 a wrong or future version is not read", async () => {
  for (const version of ["product-api/v1", "product-api/v2-draft", "p6-product-view/v1", "", null, 1]) {
    const p = { ...PROBLEMS[1], schema_version: version };
    assert.equal(readProblem(p, 401), null, String(version));
    const r = await through(p, 401);
    assert.equal(r.error.code, "access_unconfirmed");
    assert.equal(r.wipes, 0);
  }
});

test("J4 every missing or wrong-typed field fails closed", async () => {
  for (const p of PROBLEMS) {
    for (const key of Object.keys(p)) {
      const missing = { ...p };
      delete missing[key];
      assert.equal(readProblem(missing, p.status), null, `${p.code} without ${key}`);
      assert.equal(readProblem({ ...p, [key]: 42 }, p.status), null, `${p.code} with numeric ${key}`);
    }
    assert.equal(readProblem({ ...p, status: String(p.status) }, p.status), null, "status as a string");
    assert.equal(readProblem({ ...p, detail: "" }, p.status), null, "empty detail");
    assert.equal(readProblem({ ...p, detail: "x".repeat(1001) }, p.status), null, "detail over 1000");
    assert.equal(readProblem({ ...p, request_id: "-bad" }, p.status), null, "request_id pattern");
  }
  // no body, an HTML page, a JSON array: a refusal with no trustworthy reason
  for (const body of [null, "<html>", [], {}]) {
    const r = await through(body, 401);
    assert.equal(r.error.code, "access_unconfirmed");
    assert.equal(r.wipes, 0);
  }
});

test("J5 codes outside the agreed table, or outside the agreed syntax, are generic", async () => {
  const base = PROBLEMS[3];
  for (const code of ["quota_exceeded", "session.revoked", "session-revoked", "Session_revoked", "1session", "a".repeat(65)]) {
    assert.equal(readProblem({ ...base, code }, 403), null, code);
  }
  // a Problem the table does not cover still keeps the meaning of statuses that need no body
  await assert.rejects(new LiveProductPort({ fetch: async () => reply(404, { ...base, status: 404, code: "not_found" }), delay: async () => {} }).view("r"), coded("not_found"));
  await assert.rejects(new LiveProductPort({ fetch: async () => reply(500, { ...base, status: 500, code: "internal" }), delay: async () => {} }).view("r"), coded("service_failure"));
});

test("J6 a long detail is shortened for the screen without touching the code", () => {
  const long = { ...PROBLEMS[3], detail: "é".repeat(1000) };
  const e = readProblem(long, 403);
  assert.equal(e.code, "permission_denied");
  assert.equal([...e.safe_message].length, DETAIL_SHOWN);
  assert.ok(e.safe_message.endsWith("…"));
});

test("J10 detail is measured in code points, as the producer's Python counts it (Kiên, 2026-11-08)", () => {
  const p = PROBLEMS[3];
  const emoji = "😀"; // one code point, two UTF-16 units
  // 600 emoji: 600 for Python, 1200 for .length; the producer accepts it, so must the client
  const six = readProblem({ ...p, detail: emoji.repeat(600) }, 403);
  assert.equal(six.code, "permission_denied");
  assert.equal([...six.safe_message].length, DETAIL_SHOWN);
  assert.ok(!/[\uD800-\uDBFF]…$/.test(six.safe_message), "the cut never leaves half an emoji");
  assert.notEqual(readProblem({ ...p, detail: emoji.repeat(1000) }, 403), null, "1000 code points: at the limit");
  assert.equal(readProblem({ ...p, detail: emoji.repeat(1001) }, 403), null, "1001 code points: over");
  // a combining accent is its own code point in both languages: 500 × "é" is 1000, 501 is 1002
  assert.notEqual(readProblem({ ...p, detail: "é".repeat(500) }, 403), null);
  assert.equal(readProblem({ ...p, detail: "é".repeat(501) }, 403), null);
  // a family emoji is 5 code points joined by ZWJ: 200 of them is 1000, 201 is over
  const family = "👩‍👩‍👧";
  assert.equal(readProblem({ ...p, detail: family.repeat(200) }, 403).code, "permission_denied");
  assert.equal(readProblem({ ...p, detail: family.repeat(201) }, 403), null);
  // the other text fields count the same way
  assert.notEqual(readProblem({ ...p, title: emoji.repeat(256) }, 403), null);
  assert.equal(readProblem({ ...p, title: emoji.repeat(257) }, 403), null);
});

test("J7 unknown members are ignored, as RFC 9457 asks of clients", () => {
  const e = readProblem({ ...PROBLEMS[0], instance: "/v1/projects/p", retry_after: 3 }, 401);
  assert.equal(e.code, "session_expired");
});

test("J8 the client's table is the producer's table", () => {
  for (const p of PROBLEMS) assert.equal(PROBLEM_STATUS[p.code], p.status, p.code);
  for (const p of LATER) assert.equal(PROBLEM_STATUS[p.code], p.status, p.code);
  assert.deepEqual(Object.keys(PROBLEM_STATUS).sort(), [...PROBLEMS, ...LATER].map(p => p.code).sort());
});

test("J9 the local bridge keeps its own shape, and a session code there needs its status too", async () => {
  await assert.rejects(new LiveProductPort({ fetch: async () => reply(422, { code: "record_not_found", safe_message: "No." }), delay: async () => {} }).view("r"), coded("record_not_found"));
  await assert.rejects(new LiveProductPort({ fetch: async () => reply(403, { code: "forbidden", safe_message: "Wrong origin." }), delay: async () => {} }).view("r"), coded("forbidden"));
  const r = await through({ code: "session_revoked", safe_message: "Revoked." }, 403);
  assert.equal(r.error.code, "access_unconfirmed");
  assert.equal(r.wipes, 0);
});

test("K1 the producer session is read; its required fields and types are checked", () => {
  const s = fixture("session.json");
  const read = readSession(s);
  assert.equal(read.context_id, "ctx_synthetic_001");
  assert.equal(read.onboarding, "ready");
  assert.throws(() => readSession({ ...s, schema_version: "product-api/v1" }), coded("schema_unsupported"));
  for (const key of Object.keys(s)) {
    if (key === "schema_version" || key === "can_create_workspace") continue;
    const missing = { ...s };
    delete missing[key];
    assert.throws(() => readSession(missing), coded("response_invalid"), key);
  }
  const legacy = { ...s }; delete legacy.can_create_workspace;
  assert.equal(readSession(legacy).can_create_workspace, false);
  for (const allowed of [false, true]) assert.equal(readSession({ ...s, can_create_workspace: allowed }).can_create_workspace, allowed);
  for (const value of [null, 1, "true"]) assert.throws(() => readSession({ ...s, can_create_workspace: value }), coded("response_invalid"));
  for (const key of Object.keys(s.actor)) {
    const actor = { ...s.actor };
    delete actor[key];
    assert.throws(() => readSession({ ...s, actor }), coded("response_invalid"), `actor.${key}`);
  }
  const at = expires_at => ({ ...s, session: { ...s.session, expires_at } });
  // shape, then the calendar: Date.parse would roll 2026-02-30 over to 2 March (Kiên, 2026-11-08)
  for (const expires_at of ["soon", "2026-11-07", "Sat, 07 Nov 2026 12:00:00 GMT",
    "2026-02-30T12:00:00Z", "2026-02-29T00:00:00Z", "2100-02-29T00:00:00Z", "2026-04-31T00:00:00Z",
    "2026-00-10T00:00:00Z", "2026-13-01T00:00:00Z", "2026-11-00T00:00:00Z", "0000-01-01T00:00:00Z",
    "2026-11-07T24:00:00Z", "2026-11-07T12:60:00Z", "2026-11-07T12:00:60Z", "2026-11-07T12:00:00+24:00"]) {
    assert.throws(() => readSession(at(expires_at)), coded("response_invalid"), expires_at);
  }
  for (const expires_at of ["2028-02-29T00:00:00Z", "2000-02-29T23:59:59Z", "2026-12-31T23:59:59.999+07:00", "2026-11-07T12:00:00-03:30"]) {
    assert.equal(readSession(at(expires_at)).expires_at, expires_at, expires_at);
  }
  assert.throws(() => readSession({ ...s, onboarding: "pending" }), coded("response_invalid"));
  assert.equal(readSession({ ...s, last_workspace_id: null }).last_workspace_id, null);
});

test("K2 capabilities are read only for the workspace that was asked for", () => {
  const c = fixture("capabilities.json");
  const read = readCapabilities(c, "ws_synthetic_001");
  assert.equal(read.role, "analyst");
  assert.equal(read.question_chars, QUESTION_MAX, "hosted and local agree on 2000");
  assert.throws(() => readCapabilities(c, "ws_synthetic_002"), coded("response_out_of_scope"));
  assert.throws(() => readCapabilities({ ...c, schema_version: "product-api/v1" }, "ws_synthetic_001"), coded("schema_unsupported"));
  for (const key of Object.keys(c)) {
    if (key === "schema_version" || key === "workspace_id") continue;
    const missing = { ...c };
    delete missing[key];
    assert.throws(() => readCapabilities(missing, "ws_synthetic_001"), coded("response_invalid"), key);
  }
  for (const key of Object.keys(c.limits)) {
    const limits = { ...c.limits, [key]: 0 };
    assert.throws(() => readCapabilities({ ...c, limits }, "ws_synthetic_001"), coded("response_invalid"), `limits.${key}`);
  }
  assert.throws(() => readCapabilities({ ...c, deployment_mode: "local" }, "ws_synthetic_001"), coded("response_invalid"));
  assert.throws(() => readCapabilities({ ...c, membership: { role: "guest", revision: 1 } }, "ws_synthetic_001"), coded("response_invalid"));
  assert.throws(() => readCapabilities({ ...c, supported_export_formats: ["docx"] }, "ws_synthetic_001"), coded("response_invalid"));
  assert.throws(() => readCapabilities({ ...c, features: { "events.sse": "yes" } }, "ws_synthetic_001"), coded("response_invalid"));
});

test("K3 a feature the service does not list is off, and the action words match the local build", () => {
  const c = readCapabilities(fixture("capabilities.json"), "ws_synthetic_001");
  assert.equal(featureOn(c, "counterfactual"), false);
  assert.equal(featureOn(c, "prospective"), false);
  assert.equal(featureOn({ ...c, features: { "analysis.counterfactual": true } }, "counterfactual"), true);
  assert.equal(featureOn({ ...c, features: { counterfactual: true } }, "counterfactual"), false, "old client names are not read");
  assert.deepEqual(localCapabilities("live").allowed_actions, ["import", "refresh", "analyze", "follow_up", "export", "delete"]);
});

const WS = "ws_synthetic_001";
const version = "product-api/v2-draft";

test("V1 the five v5 codes are read, need their own status, and end no session", async () => {
  for (const p of LATER) {
    assert.equal(readProblem(p, p.status).code, p.code);
    assert.equal(readProblem(p, p.status === 409 ? 412 : 409), null, `${p.code} at the wrong HTTP status`);
    assert.equal(readProblem({ ...p, status: p.status === 409 ? 412 : 409 }, p.status), null, `${p.code}: the body disagrees with the response`);
    assert.equal(readProblem({ ...p, schema_version: version }, p.status), null);
    const r = await through(p, p.status);
    assert.equal(r.error.code, p.code);
    assert.deepEqual([r.after, r.wipes, r.checks], ["active", 0, 0], p.code);
  }
});

test("V2 a job receipt is for this workspace and points only at its own job", () => {
  const f = fixture("job-receipt.json");
  assert.equal(readJobReceipt(f, WS).job_id, "job_synthetic_001");
  assert.throws(() => readJobReceipt({ ...f, schema_version: version }, WS), coded("schema_unsupported"));
  assert.throws(() => readJobReceipt(f, "ws_other"), coded("response_out_of_scope"));
  for (const status_url of ["/v1/workspaces/ws_other/jobs/job_synthetic_001", "/v1/workspaces/ws_synthetic_001/jobs/other", "https://evil.example/v1/workspaces/ws_synthetic_001/jobs/job_synthetic_001", "//evil.example/x", ""]) {
    assert.throws(() => readJobReceipt({ ...f, status_url }, WS), coded("response_invalid"), status_url);
  }
  for (const key of Object.keys(f)) { if (key === "schema_version" || key === "workspace_id") continue; const m = { ...f }; delete m[key]; assert.throws(() => readJobReceipt(m, WS), coded("response_invalid"), key); }
  assert.throws(() => readJobReceipt({ ...f, status: "succeeded" }, WS), coded("response_invalid"), "202 never says it finished");
});

test("V3 a job's progress is a fraction or unknown, and unknown stays null", () => {
  const f = fixture("job.json");
  assert.equal(readJob(f, WS, f.job_id).progress, null);
  for (const progress of [0, 0.5, 1]) assert.equal(readJob({ ...f, progress }, WS, f.job_id).progress, progress);
  for (const progress of [-0.1, 1.1, "0.5", undefined, Infinity]) assert.throws(() => readJob({ ...f, progress }, WS, f.job_id), coded("response_invalid"), String(progress));
  for (const status of ["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled", "expired"]) assert.equal(readJob({ ...f, status }, WS, f.job_id).status, status);
  assert.throws(() => readJob({ ...f, status: "done" }, WS, f.job_id), coded("response_invalid"));
  assert.throws(() => readJob({ ...f, schema_version: version }, WS, f.job_id), coded("schema_unsupported"));
  assert.throws(() => readJob(f, "ws_other", f.job_id), coded("response_out_of_scope"));
  assert.throws(() => readJob(f, WS, "job_other"), coded("response_out_of_scope"));
});

test("V4 a page's total and cursor mean what they say: unknown is null, never 0", () => {
  const f = fixture("page.json");
  const page = readPage(f, WS, 200);
  assert.deepEqual([page.total, page.total_kind, page.next_cursor], [null, "unknown", null]);
  assert.equal(readPage({ ...f, total: 0, total_kind: "exact" }, WS, 200).total, 0);
  assert.equal(readPage({ ...f, total: 40, total_kind: "estimated", next_cursor: "abc" }, WS, 200).next_cursor, "abc");
  for (const bad of [{ total: 0, total_kind: "unknown" }, { total: null, total_kind: "exact" }, { total: -1, total_kind: "exact" }, { total: 1.5, total_kind: "exact" }, { total_kind: "guess" }, { next_cursor: "" }, { next_cursor: "x".repeat(4097) }, { next_cursor: 7 }, { items: {} }, { items: ["x"] }]) {
    assert.throws(() => readPage({ ...f, ...bad }, WS, 200), coded("response_invalid"), JSON.stringify(bad).slice(0, 50));
  }
  assert.throws(() => readPage(f, WS, 0), coded("response_invalid"), "more items than this workspace allows");
  assert.throws(() => readPage({ ...f, schema_version: version }, WS, 200), coded("schema_unsupported"));
  assert.throws(() => readPage(f, "ws_other", 200), coded("response_out_of_scope"));
});
