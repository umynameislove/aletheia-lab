import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const visibleResult = "Boolean(document.querySelector('.verdict')?.checkVisibility())";
const signedOut = "document.querySelector('h1')?.textContent === 'Sign in to Aletheia'";

async function eventually(read, timeoutMs) {
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    if (await read()) return performance.now() - started;
    await pause(30);
  }
  throw new Error("Browser did not reach the expected state by the deadline.");
}

async function openTab(page, port) {
  const created = await page.send("Target.createTarget", { url: "about:blank", background: true });
  assert.ok(created.result?.targetId);
  const id = created.result.targetId;
  let target;
  await eventually(async () => {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = targets.find(candidate => candidate.id === id);
    return Boolean(target?.webSocketDebuggerUrl);
  }, 5_000);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  const reasons = new Set();
  ws.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.method === "Page.backForwardCacheNotUsed") {
      for (const item of message.params.notRestoredExplanations ?? []) reasons.add(item.reason);
    }
    const action = pending.get(message.id);
    if (action) {
      pending.delete(message.id);
      if (message.error) action.reject(new Error(message.error.message));
      else action.resolve(message.result);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  await send("Page.enable");
  return {
    id, reasons, send, close: () => ws.close(),
    async read(expression) {
      const result = await send("Runtime.evaluate", { expression, returnByValue: true });
      assert.ok(!result.exceptionDetails, "Tab script failed.");
      return result.result.value;
    },
  };
}

export async function checkSessionInTwoTabs(page, fixture, fields) {
  const before = performance.now();
  const evidence = { scope: "Chrome tabs on the same origin and profile, native API logout and PostgreSQL session, seeded synthetic identity.",
    a08: { pass: false }, a09: { pass: false } };
  const tabs = [];
  try {
    const url = `${fixture.origin}/#/investigation?${fields}`;
    const peer = await openTab(page, fixture.debug_port);
    tabs.push(peer);
    const history = await openTab(page, fixture.debug_port);
    tabs.push(history);
    await peer.send("Page.navigate", { url });
    await history.send("Page.addScriptToEvaluateOnNewDocument", { source: `
      window.addEventListener("pageshow", event => {
        window.historyWitness = { persisted: event.persisted, visibleAtPageshow: Boolean(document.querySelector(".verdict")?.checkVisibility()) };
      });
    ` });
    await history.send("Page.navigate", { url });
    await eventually(() => peer.read(visibleResult), 15_000);
    await eventually(() => history.read(visibleResult), 15_000);
    const prior = await history.send("Page.getNavigationHistory");
    const entry = prior.entries[prior.currentIndex];
    assert.equal(entry.url, url);
    await history.send("Page.navigate", { url: fixture.origin + "/health/live" });
    await eventually(() => history.read("location.pathname === '/health/live'"), 10_000);
    const after = await history.send("Page.getNavigationHistory");
    assert.equal(after.entries[after.currentIndex - 1].id, entry.id);
    await page.send("Page.navigate", { url: url });
    await page.until(visibleResult, 15_000);
    await page.until("Boolean([...document.querySelectorAll('button')].find(b => b.textContent === 'Sign out' && !b.disabled))", 5_000);
    assert.equal(await peer.read(visibleResult), true);
    const logout = performance.now();
    await page.clickText("Sign out");
    await eventually(() => peer.read(signedOut), 5_000);
    const peerSignInMs = performance.now() - logout;
    assert.ok(peerSignInMs <= 5_000, "Peer tab exceeded the logout deadline.");
    assert.equal(await peer.read("document.querySelector('.verdict') === null"), true);
    const resultIdsCleared = await peer.read("!/(?:[?&](?:result|snapshot)=)/.test(location.hash)");
    await page.until(signedOut, 1_000);
    evidence.a08 = { pass: true, peerSignInMs, privateVerdictRemoved: true, resultIdsCleared };

    const back = performance.now();
    await history.send("Page.navigateToHistoryEntry", { entryId: entry.id });
    await eventually(() => history.read(signedOut), 10_000);
    const witness = await history.read("window.historyWitness ?? null");
    assert.ok(witness, "Expected an actual pageshow event.");
    assert.equal(witness.visibleAtPageshow, false, "Private result was visible at pageshow.");
    assert.equal(await history.read(visibleResult), false);
    evidence.a09 = { pass: true, elapsedMs: performance.now() - back, ...witness,
      bfcacheNotUsedReasons: [...history.reasons], witnessScope: "Visibility sampled at pageshow and after sign-in screen." };
  } finally {
    evidence.elapsedMs = performance.now() - before;
    await writeFile(fixture.session_checks_file, JSON.stringify(evidence, null, 2));
    for (const tab of tabs) tab.close();
  }
}
