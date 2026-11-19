/* The P1 fixes that only a browser can show: keys, focus and dialogs (audit 2026-11-08, KD-02..KD-05).
   Needs the demo app running and Chrome installed:
     npm run dev            # in one terminal
     npm run e2e            # in another; E2E_BASE and CHROME_PATH override the defaults */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { launch } from "./cdp.mjs";

const BASE = process.env.E2E_BASE ?? "http://127.0.0.1:5274/";
let browser, page;
before(async () => { browser = await launch(); page = browser.page; });
after(() => browser?.close());

const CLAIM = "demo-claim-001";
/** Records, after the app's own listener, whether a key press was taken over. */
const watchKeys = () => page.eval(`window.__taken = []; window.addEventListener("keydown", e => window.__taken.push(e.key + ":" + e.defaultPrevented)); true`);

test("E1 ↓ inside the reader stays in the reader; on the list it still moves between claims (KD-03)", async () => {
  await page.open(`${BASE}#/investigation?claim=${CLAIM}`);
  await watchKeys();
  await page.focus(".insp .insp-body");
  await page.key("ArrowDown");
  assert.deepEqual(await page.eval("window.__taken"), ["ArrowDown:false"], "the reader keeps its own scrolling key");
  assert.equal((await page.active()).inReader, true);
  await page.focus(`[data-claim="${CLAIM}"]`);
  await page.key("ArrowDown");
  assert.deepEqual(await page.eval("window.__taken.slice(1)"), ["ArrowDown:true"], "on the list ↓ is still the claim key");
});

test("E2 ↓ and j inside a dialog do not move the claims behind it (KD-03)", async () => {
  await page.open(`${BASE}#/investigation?claim=${CLAIM}`);
  await page.eval(`document.querySelector('button[aria-label="Keyboard shortcuts"]').click(); true`);
  await page.until("document.querySelector('[role=dialog]')");
  await watchKeys();
  await page.key("ArrowDown");
  await page.key("j");
  assert.deepEqual(await page.eval("window.__taken"), ["ArrowDown:false", "j:false"]);
  assert.equal((await page.active()).inDialog, true, "focus stays in the dialog");
});

test("E3 choosing a node in the lineage keeps focus in the reader (KD-04)", async () => {
  await page.open(`${BASE}#/investigation?claim=${CLAIM}&panel=lineage`);
  await page.until("document.querySelectorAll('.lg-node').length > 0");
  const picked = await page.eval(`(() => { const b = [...document.querySelectorAll("button.lg-node")].find(x => /evidence/i.test(x.textContent)); b.focus(); return !!b; })()`);
  assert.ok(picked, "an evidence node is shown");
  await page.key("Enter");
  await page.frames(3);
  assert.match(await page.eval("location.hash"), /evidence=demo-evidence-metric/, "the node was chosen (the panel that held focus is gone)");
  const a = await page.active();
  assert.equal(a.body, false, "focus did not fall to the page");
  assert.equal(a.inReader, true, `focus is in the reader (on ${a.tag}.${a.cls} "${a.text}")`);
});

test("E4 ticking a claim out of the 'Not reviewed' list keeps focus in the list (KD-04)", async () => {
  await page.open(`${BASE}#/investigation?filter=unchecked`);
  await page.focus(".wl .check");
  await page.key(" ");
  await page.frames(3);
  const a = await page.active();
  assert.equal(a.body, false, "focus did not fall to the page");
  assert.equal(a.id, "claims-h", "the last row left, so focus is on the claims heading");
});

test("E5 Reset review asks first; Cancel keeps every mark (KD-05)", async () => {
  await page.open(`${BASE}#/investigation`);
  await page.click(".wl .check");
  await page.until("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Reset review')");
  await page.clickText("Reset review");
  await page.until("document.querySelector('[role=dialog]')");
  assert.match(await page.eval("document.querySelector('[role=dialog]').textContent"), /Clears 1 review mark/);
  await page.eval(`[...document.querySelectorAll("[role=dialog] button")].find(b => b.textContent.trim() === "Cancel").click(); true`);
  await page.frames(2);
  assert.equal(await page.eval("document.querySelector('.wl .check').getAttribute('aria-pressed')"), "true", "Cancel kept the mark");
  await page.clickText("Reset review");
  await page.until("document.querySelector('[role=dialog]')");
  await page.eval(`[...document.querySelectorAll("[role=dialog] button")].find(b => b.textContent.trim() === "Reset review").click(); true`);
  await page.frames(2);
  assert.equal(await page.eval("document.querySelector('.wl .check').getAttribute('aria-pressed')"), "false", "confirming clears it");
});

test("E6 taking back 'I disagree' keeps the objection that was written (KD-05)", async () => {
  await page.open(`${BASE}#/investigation?claim=${CLAIM}`);
  const disagree = `[...document.querySelectorAll(".insp button")].find(b => b.textContent.includes("I disagree"))`;
  await page.eval(`${disagree}.click(); true`);
  await page.until("document.getElementById('objection')");
  await page.focus("#objection");
  await page.type("The source is from the wrong run.");
  await page.eval(`${disagree}.click(); true`);
  await page.until("!document.getElementById('objection')");
  await page.eval(`${disagree}.click(); true`);
  await page.until("document.getElementById('objection')");
  assert.equal(await page.eval("document.getElementById('objection').value"), "The source is from the wrong run.");
});

test("E7 Reset demo data and Cancel on Connect both ask first (KD-05)", async () => {
  await page.open(`${BASE}#/start`);
  await page.click("button.acct");
  await page.clickText("Reset demo data");
  await page.until("document.querySelector('[role=dialog]')");
  assert.match(await page.eval("document.querySelector('[role=dialog]').getAttribute('aria-label')"), /Reset the demo data/);
  await page.key("Escape");
  await page.until("!document.querySelector('[role=dialog]')");

  await page.open(`${BASE}#/connect`);
  await page.clickText("Choose folder");
  await page.clickText("Scan project");
  await page.until("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Cancel')");
  await page.clickText("Cancel");
  await page.until("document.querySelector('[role=dialog]')");
  await page.eval(`[...document.querySelectorAll("[role=dialog] button")].find(b => b.textContent.trim() === "Cancel").click(); true`);
  await page.frames(2);
  assert.equal(await page.eval("document.querySelector('[aria-current=step]').textContent.includes('Preview')"), true, "Cancel in the dialog keeps the mapping step");
});

test("E8 a threshold typed as 0,05 is accepted and said back as 0.05 (KD-02)", async () => {
  await page.open(`${BASE}#/connect`);
  await page.clickText("Choose folder");
  await page.clickText("Scan project");
  await page.until("document.querySelector('[aria-label^=\"Regression threshold\"]')");
  await page.focus('[aria-label^="Regression threshold"]');
  await page.type("0,05");
  const r = await page.eval(`(() => { const t = document.querySelector('[aria-label^="Regression threshold"]'); return { invalid: t.getAttribute("aria-invalid"), said: (t.getAttribute("aria-describedby") || "").split(" ").map(id => document.getElementById(id)?.textContent).join("|") }; })()`);
  assert.equal(r.invalid, "false");
  assert.match(r.said, /Read as 0\.05/);
  assert.match(await page.eval("document.querySelector('.payload').textContent"), /"regression_threshold": 0\.05/);
});
