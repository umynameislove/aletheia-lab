/* What the client does when access changes while a tab is open: when it asks, what it covers, what it
   wipes, and what it costs. Timing is driven by a fake clock.   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createSessionGuard, SESSION_TIMING as T } from "../src/service/session.ts";
import { LiveProductPort } from "../src/service/live.ts";

function setup(answers = []) {
  const clock = { t: 1_000_000 };
  const calls = [];
  const told = [];
  let wipes = 0;
  let next = { status: "ok", context_id: "ctx-a" };
  const guard = createSessionGuard({
    contextId: "ctx-a",
    now: () => clock.t,
    random: () => 0.5, // no jitter
    tell: m => told.push(m),
    onWipe: () => { wipes += 1; },
    check: async () => { calls.push(clock.t); return answers.shift() ?? next; },
  });
  return { guard, clock, calls, told, wipes: () => wipes, answer: a => { next = a; } };
}

test("V1 back to a tab after a short absence checks without hiding anything", async () => {
  const { guard, calls } = setup();
  const pending = guard.onVisible(T.hiddenCoverMs - 1);
  assert.equal(guard.state(), "active");
  await pending;
  assert.equal(calls.length, 1);
});

test("V2 after a long absence the content is covered until the server has answered", async () => {
  const { guard } = setup();
  const pending = guard.onVisible(T.hiddenCoverMs);
  assert.equal(guard.state(), "confirming", "covered before any answer exists");
  await pending;
  assert.equal(guard.state(), "active");
});

test("V3 a page restored from the back/forward cache is always covered first", async () => {
  const { guard, answer, wipes } = setup();
  answer({ status: "revoked" });
  const pending = guard.onRestored();
  assert.equal(guard.state(), "confirming");
  await pending;
  assert.equal(guard.state(), "revoked");
  assert.equal(wipes(), 1);
});

test("V4 when the check cannot be answered, what was covered stays covered", async () => {
  const { guard, answer } = setup();
  answer({ status: "unreachable" });
  await guard.onVisible(T.hiddenCoverMs);
  assert.equal(guard.state(), "unreachable", "not knowing is not being allowed");
  answer({ status: "ok", context_id: "ctx-a" });
  await guard.check(true);
  assert.equal(guard.state(), "active");
});

test("V5 a thrown check counts as unreachable, not as allowed", async () => {
  const clock = { t: 0 };
  const guard = createSessionGuard({ contextId: "a", now: () => clock.t, onWipe() {}, check: async () => { throw new Error("network"); } });
  await guard.onVisible(T.hiddenCoverMs);
  assert.equal(guard.state(), "unreachable");
});

test("V6 a visible idle tab that fails to check is left alone, and backs off", async () => {
  const { guard, clock, answer } = setup();
  answer({ status: "unreachable" });
  await guard.check(false);
  assert.equal(guard.state(), "active", "a blip does not hide what someone is reading");
  clock.t += T.idleCheckMs;
  assert.equal(guard.due(true), false, "waits longer after a failure");
  clock.t += T.idleCheckMs;
  assert.equal(guard.due(true), true);
});

test("I1 a click checks only when the last confirmation is stale", async () => {
  const { guard, clock, calls } = setup();
  clock.t += T.interactionStaleMs - 1;
  await guard.onInteraction();
  assert.equal(calls.length, 0);
  clock.t += 1;
  await guard.onInteraction();
  assert.equal(calls.length, 1);
  await guard.onInteraction();
  assert.equal(calls.length, 1, "just confirmed");
});

test("I2 a revoked session shows at the next interaction after the server refuses", async () => {
  const { guard, clock, answer, wipes } = setup();
  answer({ status: "revoked" });
  clock.t += T.interactionStaleMs;
  await guard.onInteraction();
  assert.equal(guard.state(), "revoked");
  assert.equal(wipes(), 1);
});

test("C1 an active person causes no extra traffic: every answer is already a check", async () => {
  const { guard, clock, calls } = setup();
  for (let i = 0; i < 100; i += 1) {
    clock.t += 10_000; // one business action every 10 s, as in the reference workload
    guard.noteResponse(200, null);
    await guard.onInteraction();
    assert.equal(guard.due(true), false);
  }
  assert.equal(calls.length, 0, "1000 s of activity, no session check");
});

test("C2 an idle visible tab checks about once a minute and a hidden one not at all", () => {
  const { guard, clock } = setup();
  clock.t += T.idleCheckMs - 1;
  assert.equal(guard.due(true), false);
  clock.t += 1;
  assert.equal(guard.due(true), true);
  assert.equal(guard.due(false), false, "hidden tabs do not ask");
});

test("C3 jitter spreads tabs so they do not ask in step", () => {
  const intervals = [0, 0.5, 1].map(r => {
    const clock = { t: 0 };
    const guard = createSessionGuard({ contextId: "a", now: () => clock.t, random: () => r, onWipe() {}, check: async () => ({ status: "ok", context_id: "a" }) });
    let found = -1;
    for (let t = 0; t <= T.idleCheckMs * 2 && found < 0; t += 100) { clock.t = t; if (guard.due(true)) found = t; }
    return found;
  });
  assert.ok(new Set(intervals).size === 3, String(intervals));
  assert.ok(intervals[0] >= T.idleCheckMs * (1 - T.jitter) - 100 && intervals[2] <= T.idleCheckMs * (1 + T.jitter) + 100, String(intervals));
});

test("C4 a check already in flight is shared, and a confirmation in another tab saves this one", async () => {
  const { guard, clock, calls } = setup();
  await Promise.all([guard.check(false), guard.check(false), guard.check(true)]);
  assert.equal(calls.length, 1);
  clock.t += T.idleCheckMs;
  guard.onPeer({ type: "confirmed", context_id: "ctx-a" });
  assert.equal(guard.due(true), false);
});

test("R1 the service's refusal ends the session whatever screen was open", () => {
  for (const code of ["session_revoked", "membership_revoked"]) {
    const { guard, wipes, told } = setup();
    guard.noteResponse(code === "session_revoked" ? 401 : 403, code);
    assert.equal(guard.state(), "revoked", code);
    assert.equal(wipes(), 1);
    assert.deepEqual(told.at(-1), { type: "revoked" }, "other tabs are told");
  }
});

test("R2 refused one action is not losing access", () => {
  const { guard, wipes } = setup();
  guard.noteResponse(403, "permission_denied");
  guard.noteResponse(404, "not_found");
  guard.noteResponse(500, "service_failure");
  assert.equal(guard.state(), "active");
  assert.equal(wipes(), 0);
});

test("R3 an expired session keeps the work for the same person, and wipes for anyone else", () => {
  const same = setup();
  same.guard.noteResponse(401, "session_expired");
  assert.equal(same.guard.state(), "expired");
  assert.equal(same.wipes(), 0, "the draft survives until they sign in");
  same.guard.resume("ctx-a");
  assert.equal(same.guard.state(), "active");
  assert.equal(same.wipes(), 0);

  const other = setup();
  other.guard.noteResponse(401, "session_expired");
  other.guard.resume("ctx-b");
  assert.equal(other.guard.state(), "switched");
  assert.equal(other.wipes(), 1, "their drafts never reach the next account");
});

test("R4 another account signing in on this browser wipes this tab", async () => {
  const { guard, answer, wipes } = setup();
  answer({ status: "ok", context_id: "ctx-b" });
  await guard.check(false);
  assert.equal(guard.state(), "switched");
  assert.equal(wipes(), 1);
});

test("P1 logout in one tab reaches the others at once, with no server traffic", () => {
  const { guard, wipes, calls } = setup();
  guard.onPeer({ type: "revoked" });
  assert.equal(guard.state(), "revoked");
  assert.equal(wipes(), 1);
  assert.equal(calls.length, 0);
  guard.onPeer({ type: "revoked" });
  guard.noteResponse(401, "session_revoked");
  assert.equal(wipes(), 1, "wiped once, and it cannot come back");
  guard.noteResponse(200, null);
  assert.equal(guard.state(), "revoked", "a late success cannot undo it");
});

test("P2 a peer's messages carry no content or credential", async () => {
  const { guard, told } = setup();
  await guard.check(false);
  for (const m of told) {
    assert.ok(Object.keys(m).every(k => k === "type" || k === "context_id"), JSON.stringify(m));
  }
});

test("P3 a confirmation from another account ends this one", () => {
  const { guard, wipes } = setup();
  guard.onPeer({ type: "confirmed", context_id: "ctx-b" });
  assert.equal(guard.state(), "switched");
  assert.equal(wipes(), 1);
});

test("L1 the live port reports every answer, so requests double as checks", async () => {
  const seen = [];
  const reply = (status, body) => ({ ok: status < 300, status, json: async () => body, arrayBuffer: async () => new ArrayBuffer(0) });
  const port = new LiveProductPort({
    fetch: async url => (url.endsWith("/view") ? reply(200, {}) : reply(401, { code: "session_revoked", safe_message: "Revoked." })),
    onAnswer: (status, code) => seen.push([status, code]),
  });
  await port.view("r");
  await assert.rejects(port.refresh("p"));
  assert.deepEqual(seen, [[200, null], [401, "session_revoked"]]);
});
