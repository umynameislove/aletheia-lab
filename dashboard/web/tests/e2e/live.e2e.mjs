/* The Live app against the real Python service, with replies held back, dropped or rewritten on the
   way to the browser (Q-02.03, Q-06.02, Q-07.03, Q-07.06, Q-08.02, Q-09.02, Q-10.04). Builds its own
   bundle, starts its own bridge on a temporary store, and uses synthetic projects only.
   Run: npm run e2e       E2E_PYTHON and CHROME_PATH override the defaults. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch } from "./cdp.mjs";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const REPO = resolve(WEB, "../..");
const PY = process.env.E2E_PYTHON ?? "python3";
const PORT = 8771;
const BASE = `http://127.0.0.1:${PORT}/`;
const SHOTS = process.env.E2E_SHOTS;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let tmp, bridge, browser, page, serial = 0;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "aletheia-live-"));
  execFileSync(join(WEB, "node_modules/.bin/vite"), ["build", "--mode", "live", "--outDir", join(tmp, "dist"), "--emptyOutDir"], { cwd: WEB, stdio: "ignore" });
  bridge = spawn(PY, ["-m", "aletheia_lab.dashboard", "--store-root", join(tmp, "store"), "--static", join(tmp, "dist"), "--port", String(PORT)], { cwd: REPO, env: { ...process.env, PYTHONPATH: join(REPO, "src") }, stdio: "ignore" });
  for (let i = 0; i < 100; i++) { try { if ((await fetch(BASE)).ok) break; } catch { /* not up yet */ } await sleep(100); }
  browser = await launch({ port: 9362 });
  page = browser.page;
});
after(async () => { await browser?.close(); bridge?.kill(); await sleep(200); rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

/** A fresh synthetic project each time: the service refuses a folder it has already imported. */
function project(metrics = "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\nbaseline,accuracy,0.81\ncandidate,accuracy,0.79\n") {
  const root = join(tmp, `project-${++serial}`);
  mkdirSync(root);
  const git = (...a) => execFileSync("git", a, { cwd: root, stdio: "ignore" });
  git("init", "-q"); git("config", "user.name", "Synthetic"); git("config", "user.email", "synthetic@example.invalid");
  writeFileSync(join(root, "dataset.csv"), "id,target\n1,0\n2,1\n");
  writeFileSync(join(root, "metrics.csv"), metrics);
  writeFileSync(join(root, "config.json"), '{"seed":42}\n');
  git("add", "."); git("commit", "-q", "-m", "baseline");
  return root;
}

const dialogButton = text => page.eval(`[...document.querySelectorAll("[role=dialog] button")].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})).click(); true`);
const text = () => page.eval("document.querySelector('main').innerText");
const go = async hash => { await page.eval(`location.hash = ${JSON.stringify(hash)}; true`); await page.frames(4); };

/** Connect → scan → a complete mapping, stopped just before Confirm. */
async function mapped(root) {
  await page.open(`${BASE}#/connect`);
  await page.set(".path-fld input", root);
  await page.frames(1);
  await page.clickText("Scan project");
  await page.until(`document.querySelector('[aria-label="Direction for loss"]')`);
  await page.set("#f-target-identifier-field", "id");
  await page.set("#f-baseline-run-id", "baseline");
  await page.set('[aria-label="Direction for loss"]', "lower_is_better");
  await page.set('[aria-label="Regression threshold for loss"]', "0.05");
  await page.set('[aria-label="Direction for accuracy"]', "higher_is_better");
  await page.set('[aria-label="Regression threshold for accuracy"]', "0.01");
  await page.frames(2);
}
const saved = () => page.until("document.querySelector('.lede')?.textContent.startsWith('Saved')", 8000);
async function imported() { await mapped(project()); await page.clickText("Confirm import"); await saved(); }
async function asked(q = "What changed in validation accuracy?") {
  await imported();
  await page.set('[aria-label="First question"]', q);
  await page.frames(1);
  await page.clickText("Ask");
  await page.until("location.hash.startsWith('#/investigation') && document.querySelector('h1.question')", 8000);
}
const kept = () => page.eval(`JSON.parse(localStorage.getItem("p6.opened") ?? "null")`);

test("L1 an import answered after leaving Connect leaves that screen alone, and is kept and listed", async () => {
  await mapped(project());
  const held = await page.hold("confirm_import");
  await page.clickText("Confirm import");
  await held.arrived();
  await go("#/results");
  await held.release();
  await page.until(`localStorage.getItem("p6.opened")`, 8000);
  assert.equal(await page.eval("location.hash.split('?')[0]"), "#/results", "the answer did not move the person");
  assert.match((await kept()).project_id, /^p3-project-/);
  await page.until(`document.querySelector('.late')?.innerText.includes('An import finished')`);
  await page.clickText("Continue");
  await page.until("location.hash.startsWith('#/start')");
  await page.until(`document.querySelector('main').innerText.includes('Continue your project')`);
});

test("L2 a double click on Confirm sends one request and creates one project", async () => {
  await mapped(project());
  const held = await page.hold("confirm_import");
  await page.eval(`(() => { const b = [...document.querySelectorAll("button")].find(x => x.textContent.startsWith("Confirm import")); b.click(); b.click(); return true; })()`);
  await held.arrived();
  await page.frames(4);
  assert.equal(held.seen(), 1);
  await held.release();
  await saved();
});

test("L3 a lost answer is not reported as a created project", async () => {
  await mapped(project());
  const held = await page.hold("confirm_import");
  await page.clickText("Confirm import");
  await held.arrived();
  await held.lose();
  await page.until(`document.querySelector('main').innerText.includes('outcome_unknown')`, 8000);
  const t = await text();
  assert.doesNotMatch(t, /Saved\. Ask|Snapshot stored/);
  assert.equal(await kept(), null);
});

function expireLease() {
  const find = dir => { for (const f of readdirSync(dir)) { const p = join(dir, f); if (statSync(p).isDirectory()) { const hit = find(p); if (hit) return hit; } else if (f === "project-store.sqlite3") return p; } return null; };
  const db = find(join(tmp, "store"));
  assert.ok(db && existsSync(db), "the service's store was found");
  execFileSync(PY, ["-c", `
import sqlite3, sys
from aletheia_lab.product.lifecycle import _preview_lease_sha256 as h
c = sqlite3.connect(sys.argv[1])
old = "2020-01-01T00:00:00Z"
for (pid,) in c.execute("SELECT preview_id FROM product_preview_leases").fetchall():
    c.execute("UPDATE product_preview_leases SET leased_at=?, canonical_sha256=? WHERE preview_id=?", (old, h(pid, old), pid))
c.commit()
`, db], { cwd: REPO, env: { ...process.env, PYTHONPATH: join(REPO, "src") }, stdio: "pipe" });
}

test("L4 an expired lease is refused, the mapping survives, and a new scan can be confirmed", async () => {
  await mapped(project());
  expireLease();
  await page.clickText("Confirm import");
  await page.until(`document.querySelector('main').innerText.includes('preview_expired')`, 8000);
  assert.doesNotMatch(await text(), /Saved\. Ask|Snapshot stored/);
  assert.equal(await kept(), null, "no project was reported");
  await page.clickText("Scan again and keep my choices");
  await page.until(`document.querySelector('main').innerText.includes('Scanned again')`, 8000);
  assert.equal(await page.eval(`document.querySelector('[aria-label="Regression threshold for loss"]').value`), "0.05", "the choices are still there");
  await page.clickText("Confirm import");
  await saved();
});

test("L5 a source changed after the scan cannot be confirmed into the other version", async () => {
  const root = project();
  await mapped(root);
  writeFileSync(join(root, "metrics.csv"), "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.9\nbaseline,accuracy,0.81\ncandidate,accuracy,0.79\n");
  await page.clickText("Confirm import");
  await page.until(`/preview_stale|preview_expired/.test(document.querySelector('main').innerText)`, 8000);
  assert.doesNotMatch(await text(), /Saved\. Ask|Snapshot stored/);
  assert.equal(await kept(), null);
});

test("L6 a refresh, a question and an export answered after moving on change nothing on the new screen", async () => {
  await imported();
  const projectIds = await kept();
  let held = await page.hold("refresh");
  await page.clickText("Refresh");
  await held.arrived();
  await go("#/results");
  const hash = await page.eval("location.hash");
  await held.release();
  await page.until(`document.querySelector('.late')?.innerText.includes('A refresh finished')`, 8000);
  assert.equal(await page.eval("location.hash"), hash, "a late refresh does not touch the address");
  assert.equal((await kept()).project_id, projectIds.project_id);

  await go("#/start");
  await page.until(`document.querySelector('[placeholder^="For example"]')`);
  await page.set('[placeholder^="For example"]', "What changed?");
  await page.frames(1);
  held = await page.hold("analyze_observations");
  await page.clickText("Ask");
  await held.arrived();
  await go("#/results");
  const there = await page.eval("location.hash");
  await held.release();
  await page.until(`document.querySelector('.late')?.innerText.includes('An analysis finished')`, 8000);
  assert.equal(await page.eval("location.hash"), there, "the answer waits to be opened");
});

test("L7 the export of a result you left is listed, not started on the new screen", async () => {
  await asked();
  await go(`${(await page.eval("location.hash")).replace("#/investigation", "#/retention")}`);
  await page.until(`/export/i.test(document.querySelector('main').innerText)`);
  const held = await page.hold("export_report");
  await page.clickText("Download");
  await held.arrived();
  await go("#/results");
  await held.release();
  await page.until(`document.querySelector('.late')?.innerText.includes('An export finished')`, 8000);
  assert.doesNotMatch(await text(), /Sent to your browser/);
});

test("L8 deleting: Esc waits while it runs, a refusal stays in the dialog, a retry ends at the receipt", async () => {
  await asked();
  await go((await page.eval("location.hash")).replace("#/investigation", "#/retention"));
  await page.until(`[...document.querySelectorAll("main button")].filter(b => b.textContent.includes("Delete")).length === 2`);
  const openDialog = async () => {
    await page.eval(`[...document.querySelectorAll("main button")].filter(b => b.textContent.includes("Delete"))[1].click(); true`);
    await page.until("document.querySelector('[role=dialog]')");
  };
  await openDialog();
  await page.click("[role=dialog] input[type=checkbox]");
  await page.frames(1);
  let held = await page.hold("delete_project");
  await dialogButton("Delete project");
  await held.arrived();
  await page.until(`[...document.querySelectorAll("[role=dialog] button")].some(b => b.textContent.includes("Deleting…"))`);
  await page.focus("[role=dialog] button");
  await page.key("Escape");
  assert.ok(await page.eval("!!document.querySelector('[role=dialog]')"), "Esc does not close it while deleting");
  await dialogButton("Deleting…");
  await page.frames(2);
  assert.equal(held.seen(), 1, "a second press sends nothing");
  await held.answer(503, { code: "bridge_busy", safe_message: "The service is busy." });
  await page.until(`document.querySelector('[role=dialog] [role=alert]')`, 8000);
  assert.ok(await page.eval("!!document.querySelector('[role=dialog]')"), "the refusal is read in the dialog");
  await dialogButton("Delete project");
  await page.until(`document.querySelector('main').innerText.includes('Deletion receipt') || document.querySelector('main').innerText.includes('DELETION RECEIPT')`, 8000);
  assert.equal(await page.eval("document.querySelector('[role=dialog]')"), null);
  const a = await page.active();
  assert.equal(a.body, false, "focus went to the receipt");
  assert.match(a.text, /Deletion receipt/i);
  assert.equal(await kept(), null, "the deleted project is no longer offered");
});

/* A view the real service answered, with more in it than a two-row synthetic project has. */
const LONG = "độ_chính_xác_".repeat(40) + "😀".repeat(60) + "é".repeat(40);
function dense(view) {
  const claim = view.claims[0], ev = view.evidence[0];
  const extra = Array.from({ length: 64 }, (_, i) => ({ ...claim, id: `${claim.id}-x${i}`, text: i === 0 ? LONG : `${claim.text} (${i + 2})` }));
  const sources = [
    { ...ev, id: `${ev.id}-long`, title: LONG, text: LONG + " " + "Dòng nguồn tiếng Việt có dấu. ".repeat(60) },
    { ...ev, id: `${ev.id}-nfd`, title: "Nguồn NFD".normalize("NFD"), text: "Độ chính xác giảm".normalize("NFD") },
    { ...ev, id: `${ev.id}-withheld`, title: "Withheld source", text: "Only this part can be shown.", redacted: true },
  ];
  claim.citation_ids = [...claim.citation_ids, ...sources.map(s => s.id)];
  return { ...view, claims: [claim, ...extra], evidence: [...view.evidence, ...sources] };
}
const shot = async name => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.shot(join(SHOTS, name), true); } };
const overflow = () => page.eval("document.documentElement.scrollWidth - innerWidth");
const reopenWith = async () => { await page.send("Page.reload"); await sleep(300); await page.until("document.querySelector('h1.question')", 8000); await page.frames(4); };

test("L9 65 claims page and search; the last page's Next stays focusable and says so", async () => {
  await asked();
  await page.rewrite("view", dense);
  await reopenWith();
  assert.match(await page.eval("document.querySelector('.pager .shown').textContent"), /1–\d+ of 65 claims/);
  await shot("dense-investigation-light-1440.png");
  const pages = +(await page.eval(`document.querySelector('.pager').innerText.match(/Page \\d+ of (\\d+)/)[1]`));
  for (let i = 1; i < pages; i++) { await page.eval(`[...document.querySelectorAll(".pager button")].at(-1).click(); true`); await page.frames(2); }
  await page.focus(".pager button:last-of-type");
  assert.equal(await page.eval(`[...document.querySelectorAll(".pager button")].at(-1).getAttribute("aria-disabled")`), "true");
  await page.key("Enter");
  const a = await page.active();
  assert.equal(a.body, false, "pressing Next on the last page does not drop focus");
  assert.match(await page.eval("document.querySelector('.pager').innerText"), new RegExp(`Page ${pages} of ${pages}`));
  await page.set('[aria-label="Search claims and citations"]', "(9)");
  await page.until(`document.querySelector('.wl').children.length === 1 && !document.querySelector('.pager')`, 3000);
  await page.unroute();
});

test("L10 long Unicode, NFD and a withheld source are readable without breaking the page (Q-09.02)", async () => {
  await asked();
  await page.rewrite("view", dense);
  await reopenWith();
  const claim = await page.eval(`document.querySelector("[data-claim]").dataset.claim`);
  await go(`${await page.eval("location.hash")}&claim=${encodeURIComponent(claim)}`);
  await page.until(`document.querySelectorAll(".src-chip").length >= 4`, 6000);
  await page.eval(`[...document.querySelectorAll(".src-chip")].find(b => b.textContent.startsWith("độ_chính")).click(); true`);
  await page.frames(3);
  assert.equal(await overflow(), 0, "no sideways page at 1440");
  assert.equal(await page.eval(`(() => { const t = document.querySelector(".src-title"), p = t.parentElement.getBoundingClientRect(); return t.scrollWidth <= t.clientWidth + 1 && t.getBoundingClientRect().right <= p.right + 1; })()`), true, "a long title stays inside its panel");
  assert.equal(await page.eval(`document.querySelector(".src-text").textContent.includes("😀😀")`), true);
  await shot("long-unicode-source-light-1440.png");
  await page.resize(320, 700);
  await page.frames(4);
  assert.equal(await overflow(), 0, "no sideways page at 320");
  await shot("long-unicode-source-light-320.png");
  await page.resize(1440, 900);
  await page.frames(2);
  await page.eval(`[...document.querySelectorAll(".src-chip")].find(b => b.textContent.includes("Withheld")).click(); true`);
  await page.until(`document.querySelector('.insp-body').innerText.includes('Part of this source is withheld')`);
  await page.eval(`[...document.querySelectorAll(".src-chip")].find(b => b.textContent === "Nguồn NFD".normalize("NFD") || b.textContent.normalize("NFC") === "Nguồn NFD".normalize("NFC")).click(); true`);
  await page.until(`document.querySelector('.src-text')?.textContent.normalize("NFC") === "Độ chính xác giảm".normalize("NFC")`);
  // a source that is not in this result: said, nothing shown in its place, and a way back
  await go((await page.eval("location.hash")).replace(/evidence=[^&]*/, "evidence=no-such-source"));
  await page.until(`document.querySelector('.insp-body').innerText.includes('That source is not in this result')`);
  await page.clickText("Show this claim");
  await page.until(`!document.querySelector('.insp-body').innerText.includes('That source is not in this result')`);
  await page.unroute();
});

test("L11 a graph with a cycle draws, its table lists every edge, and it does not leak into the next result (Q-10.04)", async () => {
  await asked();
  const looped = view => {
    const [a, b] = view.graph.nodes;
    return { ...view, graph: { ...view.graph, edges: [...view.graph.edges, { id: "cycle-1", kind: "CITES", source: a.id, target: b.id, visibility: "visible" }, { id: "cycle-2", kind: "CITES", source: b.id, target: a.id, visibility: "visible" }, { id: "self", kind: "CITES", source: a.id, target: a.id, visibility: "visible" }] } };
  };
  await page.rewrite("view", looped);
  await reopenWith();
  await go(`${await page.eval("location.hash")}&panel=lineage`);
  await page.until("document.querySelectorAll('.lg-node').length > 0", 6000);
  await page.eval(`document.querySelector("details.lg-table, .insp-body details")?.setAttribute("open", ""); true`);
  await page.frames(2);
  assert.match(await page.eval("document.querySelector('.insp-body').innerText"), /Relations, as a table/);
  assert.equal(await page.eval(`[...document.querySelectorAll(".tbl--edges tbody tr")].length >= 3`), true, "the cycle's edges are listed");
  await page.unroute();
  const dangling = view => ({ ...view, graph: { ...view.graph, edges: [...view.graph.edges, { id: "broken", kind: "CITES", source: view.graph.nodes[0].id, target: "nowhere", visibility: "visible" }] } });
  await page.rewrite("view", dangling);
  await page.send("Page.reload");
  await sleep(600);
  await page.until(`document.body.innerText.includes('Lineage not drawn') || document.body.innerText.includes('response_invalid')`, 6000);
  assert.equal(await page.eval("document.querySelectorAll('.lg-node').length"), 0, "a graph that cannot be trusted is not partly drawn");
  await page.unroute();
});

test("L12 removed, added and unknown metrics read as what they are, never as 0 (Q-08.02)", async () => {
  await asked();
  const shaped = view => {
    const c = view.snapshot.metric_changes[0];
    const base = c ?? { evidence_id: view.evidence[0].id, metric_name: "accuracy", kind: "increased", adverse_status: "not_applicable", delta: null, before: null, after: null };
    const obs = v => ({ snapshot_id: view.snapshot.id, run_id: "candidate", step: null, value: v });
    return { ...view, snapshot: { ...view.snapshot, metric_changes: [
      { ...base, metric_name: "tiny_m", kind: "increased", before: obs(1e-7), after: obs(2e-7), delta: 1e-7, adverse_status: "not_adverse" },
      { ...base, metric_name: "removed_m", kind: "removed", before: obs(0.5), after: null, delta: null, adverse_status: "not_applicable" },
      { ...base, metric_name: "added_m", kind: "added", before: null, after: obs(0.5), delta: null, adverse_status: "not_applicable" },
    ] } };
  };
  await page.rewrite("view", shaped);
  await reopenWith();
  const rows = await page.eval(`[...document.querySelectorAll("main")].map(m => m.innerText).join("")`);
  await shot("metrics-removed-added-tiny-light-1440.png");
  assert.match(rows, /removed_m/);
  assert.match(rows, /added_m/);
  assert.match(rows, /1e-7/, "a tiny value is not rounded to 0");
  assert.match(rows, /No value/, "a side with no observation is not 0");
  assert.match(rows, /No change to compare/);
  // the screen follows the locale; the exact values can still be read and copied
  const claim = await page.eval(`document.querySelector("[data-claim]").dataset.claim`);
  await go(`${await page.eval("location.hash")}&claim=${encodeURIComponent(claim)}`);
  await page.until(`document.querySelector(".src-foot")`, 6000);
  await page.eval(`document.querySelector(".insp-body details.meta-toggle").open = true; [...document.querySelectorAll(".insp-body details")].forEach(d => d.open = true); true`);
  assert.match(await page.eval("document.querySelector('.insp-body').innerText"), /before 1e-7, after 2e-7, delta 1e-7/);
  await page.unroute();
});
