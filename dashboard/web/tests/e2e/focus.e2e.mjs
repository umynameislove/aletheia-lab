/* Focus, dialogs and drawers as a keyboard user meets them (Q-14.02), toasts that wait to be read
   (Q-02.03), and stored preferences that are not what this build wrote (Q-06.04).
   Needs the demo app running: npm run dev, then npm run e2e. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { launch } from "./cdp.mjs";

const BASE = process.env.E2E_BASE ?? "http://127.0.0.1:5274/";
const CLAIM = "demo-claim-001";
let browser, page;
before(async () => { browser = await launch({ port: 9363 }); page = browser.page; });
after(() => browser?.close());
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shortcuts = `document.querySelector('button[aria-label="Keyboard shortcuts"]')`;

test("F1 a dialog takes focus, keeps Tab inside, closes on Esc and gives focus back", async () => {
  await page.open(`${BASE}#/start`);
  await page.eval(`${shortcuts}.focus(); ${shortcuts}.click(); true`);
  await page.until("document.querySelector('[role=dialog]')");
  await page.frames(2);
  assert.equal((await page.active()).inDialog, true);
  const count = await page.eval(`document.querySelectorAll("[role=dialog] button").length`);
  for (let i = 0; i < count + 2; i++) { await page.key("Tab"); assert.equal((await page.active()).inDialog, true, `Tab ${i + 1} stays inside`); }
  await page.key("Escape");
  await page.until("!document.querySelector('[role=dialog]')");
  await page.frames(2);
  assert.equal((await page.active()).text, "Keyboard shortcuts", "focus is back on what opened it");
});

test("F2 on a narrow screen the reader is a modal drawer: focus enters, is kept, and returns", async () => {
  await page.resize(600, 800);
  await page.open(`${BASE}#/investigation`);
  await page.focus(`[data-claim="${CLAIM}"]`);
  await page.key("Enter");
  await page.until(`document.querySelector('.insp--drawer.open')`);
  await page.frames(3);
  assert.equal((await page.active()).inReader, true, "focus moved into the drawer");
  for (let i = 0; i < 14; i++) { await page.key("Tab"); assert.equal((await page.active()).inReader, true, `Tab ${i + 1} stays in the drawer`); }
  await page.click(".insp--drawer .insp-head .icon-btn");
  await page.until(`!document.querySelector('.insp--drawer.open')`);
  await page.frames(3);
  const a = await page.active();
  assert.equal(a.body, false, "focus was handed back");
  assert.match(a.text, /validation accuracy/);
});

test("F3 a closed drawer cannot take focus or be read", async () => {
  await page.resize(600, 800);
  await page.open(`${BASE}#/investigation`);
  assert.equal(await page.eval(`document.querySelector('.insp--drawer').hasAttribute('inert')`), true);
  await page.eval("document.activeElement.blur(); true");
  for (let i = 0; i < 30; i++) { await page.key("Tab"); assert.equal((await page.active()).inReader, false); }
  await page.resize(1440, 900);
});

test("F4 confirming with something missing goes to the first field that needs it", async () => {
  await page.open(`${BASE}#/connect`);
  await page.clickText("Choose folder");
  await page.clickText("Scan project");
  await page.until(`[...document.querySelectorAll("button")].some(b => b.textContent.startsWith("Confirm import"))`);
  await page.clickText("Confirm import");
  await page.until(`document.getElementById("confirm-errors")`);
  await page.frames(3);
  assert.equal(await page.eval(`document.activeElement.getAttribute("aria-invalid")`), "true", "focus is on a field marked invalid");
  const r = await page.eval(`(() => { const a = document.activeElement.getBoundingClientRect(); const hit = document.elementFromPoint(a.left + a.width / 2, a.top + a.height / 2); return { inView: a.top >= 0 && a.bottom <= innerHeight, mine: hit === document.activeElement || document.activeElement.contains(hit) }; })()`);
  assert.deepEqual(r, { inView: true, mine: true }, "it is on screen and nothing covers it");
});

test("F5 focus stays on screen and uncovered when the page reflows under it", async () => {
  await page.open(`${BASE}#/investigation`);
  await page.focus(`[aria-label="Search claims and citations"]`);
  await page.resize(320, 500);
  await page.frames(4);
  const r = await page.eval(`(() => { document.activeElement.scrollIntoView({ block: "nearest" }); const a = document.activeElement.getBoundingClientRect(); const hit = document.elementFromPoint(a.left + a.width / 2, a.top + a.height / 2); return { same: document.activeElement.getAttribute("aria-label"), mine: hit === document.activeElement || document.activeElement.contains(hit) || hit.closest(".search") !== null }; })()`);
  assert.deepEqual(r, { same: "Search claims and citations", mine: true });
  await page.resize(1440, 900);
});

test("F6 a toast waits while it is being read, and can be closed", async () => {
  await page.open(`${BASE}#/connect`);
  await page.clickText("Choose folder");
  await page.clickText("Scan project");
  await page.until(`document.querySelector(".toast")`);
  const box = await page.eval(`(() => { const r = document.querySelector(".toast").getBoundingClientRect(); return { x: r.left + 20, y: r.top + 10 }; })()`);
  await page.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x, y: box.y });
  await sleep(5800);
  assert.equal(await page.eval(`!!document.querySelector(".toast")`), true, "still there after its 5 seconds, because the pointer is on it");
  await page.focus(`.toast button`);
  await page.key("Enter");
  await page.until(`!document.querySelector(".toast")`);
});

test("F7 stored preferences this build did not write fall back to the defaults", async () => {
  await page.open(`${BASE}#/start`);
  await page.eval(`localStorage.setItem("p6-theme", "<b>"); localStorage.setItem("p6-density", "99"); localStorage.setItem("p6-terms", "{}"); localStorage.setItem("p6-letter-keys", "maybe"); localStorage.setItem("p6.opened", '{"v":9,"project_id":"p","snapshot_id":"s"}'); true`);
  await page.send("Page.reload");
  await sleep(500);
  await page.until("document.querySelector('h1')");
  const r = await page.eval(`({ theme: document.documentElement.dataset.theme, density: document.documentElement.dataset.density ?? null, terms: document.documentElement.dataset.terms ?? null })`);
  assert.ok(["light", "dark"].includes(r.theme));
  assert.deepEqual([r.density, r.terms], [null, null]);
  const stored = await page.eval("Object.keys(localStorage).sort()");
  for (const k of stored) assert.match(k, /^p6[-.]/, "nothing else is kept");
  assert.equal(await page.eval(`/token|bearer|secret/i.test(JSON.stringify(localStorage))`), false);
});

test("F8 with nothing selected the person meets the answer and the claims, not an empty panel or a hash (Q-02.04)", async () => {
  await page.resize(1440, 900);
  await page.open(`${BASE}#/investigation`);
  const r = await page.eval(`(() => { const main = document.querySelector("main"); const insp = document.querySelector(".insp"); return { reader: insp ? getComputedStyle(insp).display : "none", hash: /[0-9a-f]{32,}/.test(main.innerText), answerAbove: document.querySelector(".verdict").getBoundingClientRect().bottom <= innerHeight, claimsInView: document.querySelector(".wl").getBoundingClientRect().top < innerHeight }; })()`);
  assert.deepEqual(r, { reader: "none", hash: false, answerAbove: true, claimsInView: true });
});

const SCREENS = ["start", "connect", "project", "investigation", "results", "retention", "counterfactual", "prospective"];
const touch = on => page.send("Emulation.setTouchEmulationEnabled", { enabled: on, maxTouchPoints: on ? 5 : 0 });

test("F9 by touch at 320 px: a tap opens the drawer over the claim's place, the scrim closes it (Q-09.04, Q-14.04)", async () => {
  await touch(true);
  await page.resize(320, 640);
  await page.open(`${BASE}#/investigation`);
  await page.tap(`[data-claim="${CLAIM}"]`);
  await page.until(`document.querySelector('.insp--drawer.open')`);
  await page.frames(3);
  assert.equal(await page.eval("document.documentElement.scrollWidth - innerWidth"), 0);
  const contained = await page.eval(`getComputedStyle(document.querySelector('.insp-body')).overscrollBehaviorY`);
  assert.equal(contained, "contain", "scrolling to the end of the drawer does not move the page behind");
  const box = await page.eval(`(() => { const r = document.querySelector('.insp--drawer').getBoundingClientRect(); return { left: r.left, width: r.width }; })()`);
  await page.eval(`document.querySelector('.insp-scrim').click(); true`);
  await page.until(`!document.querySelector('.insp--drawer.open')`);
  await page.frames(2);
  assert.equal(await page.eval(`document.querySelector('.insp--drawer').hasAttribute('inert')`), true, "closed, it holds no focus");
  assert.ok(box.width <= 320);
  await touch(false);
  await page.resize(1440, 900);
});

test("F10 every control a finger can reach is at least 24 by 24 px, in portrait and in landscape (Q-14.04)", async () => {
  await touch(true);
  const small = [];
  for (const [w, h] of [[390, 844], [844, 390]]) {
    await page.resize(w, h);
    for (const s of SCREENS) {
      await page.open(`${BASE}#/${s}`);
      const found = await page.eval(`[...document.querySelectorAll("a[href], button, input, select, summary, [role=switch], [role=tab]")].filter(e => { const r = e.getBoundingClientRect(); const c = getComputedStyle(e); return r.width > 1 && r.height > 1 && c.visibility !== "hidden" && !e.closest("[inert], .sr") && !e.closest("p, li.txt") && e.className !== "skip" && (r.width < 24 || r.height < 24); }).map(e => (e.getAttribute("aria-label") || e.textContent || e.tagName).trim().slice(0, 30) + " " + Math.round(e.getBoundingClientRect().width) + "x" + Math.round(e.getBoundingClientRect().height))`);
      for (const f of found) small.push(`${s} ${w}: ${f}`);
      assert.equal(await page.eval("document.documentElement.scrollWidth - innerWidth"), 0, `${s} at ${w}x${h}`);
    }
  }
  await touch(false);
  await page.resize(1440, 900);
  assert.deepEqual(small, []);
});

test("F11 in landscape the composer does not cover the content it follows (Q-14.04)", async () => {
  await page.resize(844, 390);
  await page.open(`${BASE}#/investigation`);
  const r = await page.eval(`(() => { const c = document.querySelector(".composer-wrap"), cs = getComputedStyle(c); const box = c.getBoundingClientRect(); return { position: cs.position, tall: box.height / innerHeight }; })()`);
  assert.ok(r.position === "static" || r.tall < 0.5, JSON.stringify(r));
  await page.resize(1440, 900);
});
