/* Accessibility guards (Q12) that can be checked from source. The browser audit — contrast,
   390px and 200% zoom reflow, keyboard order — is recorded in docs/p6-dashboard-quickstart.md.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { warnBeforeUnload } from "../src/service/unsaved.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = p => readFileSync(join(ROOT, p), "utf8");
const components = readdirSync(join(ROOT, "src/components")).filter(f => f.endsWith(".tsx")).map(f => [f, read(`src/components/${f}`)]);

test("A1 index.html has no inline script, so the bridge CSP (script-src 'self') runs everything", () => {
  const html = read("index.html");
  assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>/.test(html), "every <script> loads from a file");
  assert.match(html, /<script src="\/theme-init\.js"><\/script>/);
});

test("A2 dialogs keep focus inside, close on Escape and return focus", () => {
  const modal = read("src/components/Modal.tsx");
  assert.match(modal, /role="dialog" aria-modal="true"/);
  assert.match(modal, /e\.key === "Escape"/);
  assert.match(modal, /e\.key !== "Tab"/);
  // focus returns only to an element still in the page (a deleted project's row is gone), else to the screen
  assert.match(modal, /return \(\) => \{ if \(prev\?\.isConnected\) prev\.focus\(\); else document\.getElementById\("main"\)\?\.focus\(\); \};/);
});

test("A3 icon-only buttons have an accessible name", () => {
  for (const [f, src] of components) {
    for (const m of src.matchAll(/<button className="icon-btn[^"]*"[^>]*>/g)) assert.match(m[0], /aria-label=/, `${f}: ${m[0].slice(0, 60)}`);
  }
});

test("A4 decorative separators are hidden from assistive tech", () => {
  for (const [f, src] of components) {
    for (const m of src.matchAll(/<span className="sep"[^>]*>/g)) assert.match(m[0], /aria-hidden="true"/, f);
  }
});

test("A5 motion is off when the system asks, and focus stays visible", () => {
  const css = read("src/styles/app.css");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\*, \*::before, \*::after \{ transition: none !important; animation: none !important; \}/);
  assert.match(css, /:focus-visible \{ outline: 2px solid var\(--accent\)/);
  assert.match(read("src/App.tsx"), /className="skip" href="#main"/);
});

test("A6 opening another result is a link: new tab and copy work, and a click adds one history step", () => {
  for (const f of ["Results.tsx", "Late.tsx", "Start.tsx"]) {
    const src = read(`src/components/${f}`);
    // the old way wrote the address twice (linkIds, then go), so Back landed on the wrong result
    assert.ok(!/linkIds\(/.test(src), `${f} does not write the address itself`);
  }
  for (const f of ["Results.tsx", "Late.tsx"]) {
    assert.ok(!/<button[^>]*onClick=\{[^}]*go\(/.test(read(`src/components/${f}`)), `${f}: no button navigates`);
  }
  assert.match(read("src/components/Start.tsx"), /<a className="btn btn--primary" href=\{hrefFor\("investigation", \{ project: snap\.project_id/);
  const results = read("src/components/Results.tsx");
  assert.equal((results.match(/<a className="btn btn--sm" href=\{hrefFor\("investigation", \{/g) ?? []).length, 2, "both Open controls are links");
  // the open result is said in words, not offered as a link to itself
  assert.match(results, /<span className="v">Open now<\/span>/);
  assert.match(read("src/components/Late.tsx"), /onClick=\{e => \{ if \(plainClick\(e\)\) dismissLate\(w\.id\); \}\}/);
});

test("A7 every screen that scrolls has something to tab to (axe scrollable-region-focusable)", () => {
  const results = read("src/components/Results.tsx");
  // sample data hides the list of stored results; the panel in its place still offers a link
  assert.match(results, /\{v\.demo_only && \(/);
  assert.match(results, /href=\{hrefFor\("connect"\)\}>Connect a project<\/a>/);
  assert.match(results, /href=\{hrefFor\("investigation"\)\}>Open the investigation<\/a>/);
  const gated = read("src/components/Gated.tsx");
  assert.equal((gated.match(/after=\{<BackToWork \/>\}/g) ?? []).length, 3, "Counterfactual (both states) and Prospective");
});

test("A8 unsaved review marks: the leave warning is on only while it is attached", () => {
  const win = new EventTarget();
  // Node before 23 gives Event a returnValue getter but no setter (Kiên saw a late TypeError on 22.13);
  // a browser's BeforeUnloadEvent has both, so the stand-in carries its own writable field
  class BeforeUnload extends Event { returnValue = undefined; }
  const leave = () => { const e = new BeforeUnload("beforeunload", { cancelable: true }); win.dispatchEvent(e); return e.defaultPrevented; };
  assert.equal(leave(), false, "no marks: no listener, no warning");
  const stop = warnBeforeUnload(win);
  assert.equal(leave(), true, "marks: the browser is asked to warn");
  stop();
  assert.equal(leave(), false, "marks cleared: the listener is gone");
  // both signals are set: preventDefault for current browsers, returnValue for older ones
  let listener = null, prevented = false;
  const spy = { addEventListener: (_t, l) => { listener = l; }, removeEventListener: () => {} };
  warnBeforeUnload(spy);
  const e = { preventDefault() { prevented = true; }, returnValue: undefined };
  listener(e);
  assert.equal(prevented, true);
  assert.equal(e.returnValue, "");
  // App attaches it only while there are marks, and detaches on cleanup
  assert.match(read("src/App.tsx"), /const unsaved = Object\.keys\(s\.review\)\.length > 0;\s*useEffect\(\(\) => \(unsaved \? warnBeforeUnload\(window\) : undefined\), \[unsaved\]\);/);
});

test("A9 copying is announced once, by a status beside the button, and the label coming back is not", () => {
  const reader = read("src/components/Reader.tsx");
  const copy = reader.slice(reader.indexOf("function CopyButton"), reader.indexOf("function Review"));
  assert.ok(!/aria-live/.test(copy), "the button is not a live region");
  assert.match(copy, /<span className="sr" role="status">\{said\}<\/span>/);
  // a second copy empties the status before saying it again, or the same text would not be read twice
  assert.match(copy, /setSaid\(""\);\s*window\.clearTimeout\(sayTimer\.current\);\s*sayTimer\.current = window\.setTimeout\(\(\) => setSaid\(/);
  // the write comes first in the click, before any await
  assert.match(copy, /const value = text\(\);\s*let outcome: "done" \| "failed";\s*try \{ await navigator\.clipboard\.writeText\(value\)/);
});

test("A10 single-key shortcuts can be turned off (WCAG 2.1.4); arrows, Esc and the lineage's focus-scoped keys stay", () => {
  const app = read("src/App.tsx");
  assert.match(app, /e\.key === "\?" && !typing && letterKeys/);
  assert.match(app, /role="switch" aria-checked=\{letterKeys\} onClick=\{toggleLetterKeys\}/);
  const inv = read("src/components/Investigation.tsx");
  assert.match(inv, /if \(!letterKeys && e\.key !== "ArrowDown" && e\.key !== "ArrowUp"\) return;/);
  // the gate comes before any letter is handled
  assert.ok(inv.indexOf("if (!letterKeys") < inv.indexOf('if (e.key === "/")'));
  assert.match(read("src/store.tsx"), /localStorage\.getItem\("p6-letter-keys"\) !== "off"/);
});

test("A11 a focused field shows the shared ring, even while marked invalid and in forced colours (Q-02.03 C1, M15)", () => {
  const css = read("src/styles/app.css");
  // no field rule switches the outline off; the ring is the global :focus-visible one
  for (const m of css.matchAll(/^[^{}]*\b(?:input|select|textarea)\b[^{}]*:focus[^{}]*\{[^}]*\}/gm)) {
    assert.ok(!/outline:\s*none/.test(m[0]), m[0]);
  }
  // the invalid rule only recolours the border, so it cannot hide the ring
  assert.match(css, /\.input\[aria-invalid="true"\], \.select\[aria-invalid="true"\] \{ border-color: var\(--no\); \}/);
  const forced = css.slice(css.indexOf("@media (forced-colors: active)"));
  assert.match(forced, /\.input:focus-visible, \.select:focus-visible \{ outline: 2px solid Highlight;/);
  // a disabled field looks it, and does not answer hover
  assert.match(css, /\.input:hover:not\(:disabled\), \.select:hover:not\(:disabled\)/);
  assert.match(css, /\.input:disabled, \.select:disabled \{ opacity: \.45; cursor: not-allowed; \}/);
});

test("A12 deleting a project: busy in the dialog, cannot be pressed twice or closed, and a refusal stays in it (Q-02.03 C2, C3, M4)", () => {
  const ret = read("src/components/Retention.tsx");
  const dialog = ret.slice(ret.indexOf("function ConfirmDelete"));
  assert.match(dialog, /<Modal [^>]*busy=\{busy\}>/);
  assert.match(dialog, /aria-disabled=\{busy \|\| undefined\} aria-busy=\{busy\} onClick=\{\(\) => \{ if \(!busy\) onConfirm\(\); \}\}/);
  assert.match(dialog, /\{busy \? "Deleting…" :/);
  assert.match(dialog, /<RefusalNote refusal=\{refusal\} \/>/);
  // the handler guards too, before any await: a second press can come before the re-render
  assert.match(ret, /if \(scope !== "project" \|\| deleting\.current\) return;/);
  // a failure is not a toast, and does not close the dialog; only success closes it
  const del = ret.slice(ret.indexOf("const del = async"), ret.indexOf("return (", ret.indexOf("const del = async")));
  assert.ok(!/toast\(`Refused/.test(ret), "no refusal toast");
  assert.match(del, /catch \(e\) \{[\s\S]*setRefusal\(/);
  assert.ok(!/finally \{[^}]*setConfirm\(null\)/.test(del), "the dialog is not closed whatever happened");
  // after success, focus lands on the receipt, which can take it
  assert.match(ret, /aria-label="Deletion receipt" tabIndex=\{-1\} ref=\{receiptRef\}/);
  // Esc, the scrim and the close button do nothing while busy
  const modal = read("src/components/Modal.tsx");
  assert.match(modal, /const close = \(\) => \{ if \(!busy\) onClose\(\); \};/);
  assert.match(modal, /if \(e\.key === "Escape"\) \{ e\.stopPropagation\(\); close\(\); return; \}/);
  assert.match(modal, /if \(e\.target === e\.currentTarget\) close\(\);/);
});

test("A13 a control that is busy or at a limit keeps focus: aria-disabled and a guarded handler, not disabled (Q-02.03 M1, M2, L6)", () => {
  const pager = read("src/components/Worklist.tsx");
  const p = pager.slice(pager.indexOf("export function Pager"));
  assert.ok(!/disabled=\{page\.page/.test(p), "the pager does not disable a focused button");
  assert.match(p, /aria-disabled=\{first \|\| undefined\} onClick=\{\(\) => \{ if \(!first\) onPage\(page\.page - 1\); \}\}/);
  assert.match(p, /aria-disabled=\{last \|\| undefined\} onClick=\{\(\) => \{ if \(!last\) onPage\(page\.page \+ 1\); \}\}/);
  const inv = read("src/components/Investigation.tsx");
  const composer = inv.slice(inv.indexOf("function Composer"), inv.indexOf("function FailedRun"));
  assert.match(composer, /value=\{text\} readOnly=\{busy\} disabled=\{sample \|\| !!followOff\}/);
  assert.match(composer, /aria-disabled=\{!can \|\| undefined\} aria-busy=\{busy\} onClick=\{ask\}/);
  assert.match(composer, /if \(followOff \|\| busy \|\| sample \|\| !selection \|\| !question\) return;/);
  // hosted follow-up is gated by the workspace binding, not by the build
  assert.match(composer, /const followOff = hostedOff\("follow_up"\);/);
  assert.ok(!/disabled=\{!can\}|disabled=\{busy/.test(composer));
  // the error and the missing selection are tied to the box (M9, M10)
  assert.match(composer, /aria-invalid=\{!!\(err \|\| problem\)\} aria-describedby=\{note \? "composer-err" : undefined\}/);
  assert.match(composer, /Select a claim or node first\./);
  // one style for aria-disabled on every button, and hover/press do not apply to it
  const css = read("src/styles/app.css");
  assert.match(css, /\.btn:disabled, \.btn\[aria-disabled="true"\], \.icon-btn\[aria-disabled="true"\] \{ opacity: \.45;/);
  assert.match(css, /\.btn:active:not\(:disabled\):not\(\[aria-disabled="true"\]\)/);
  assert.ok(!/\.lg-tools \[aria-disabled/.test(css), "no lineage-only copy of the rule");
});

test("A14 the account menu is a disclosure: no menu roles, the panel follows its button, Esc closes only it and returns focus (Q-02.03 M5, L9)", () => {
  const rail = read("src/components/Rail.tsx");
  const acct = rail.slice(rail.indexOf("function Account"));
  assert.ok(!/role="menu|menuitem|aria-haspopup/.test(acct), "no menu roles without menu keys");
  assert.match(acct, /<button ref=\{btn\} className="acct" aria-expanded=\{open\} aria-controls=\{open \? panel : undefined\}/);
  // the panel comes after the button in the DOM, so Tab goes from the button into it
  assert.ok(acct.indexOf('className="acct"') < acct.indexOf('className="acct-menu"'));
  assert.match(acct, /<div className="acct-menu" id=\{panel\}/);
  assert.equal((acct.match(/role="switch" aria-checked=/g) ?? []).length, 2, "both toggles stay switches");
  // Esc is handled on the wrapper and stopped there, so App's window listener does not also close the drawer
  assert.match(acct, /const onKey = \(e: ReactKeyboardEvent\) => \{ if \(e\.key === "Escape" && open\) \{ e\.stopPropagation\(\); close\(\); \} \};/);
  assert.match(acct, /<div className="acct-wrap" ref=\{ref\} onKeyDown=\{onKey\}>/);
  assert.ok(!/document\.addEventListener\("keydown"/.test(acct));
  // closing by Esc or by an action hands focus back to the button
  assert.match(acct, /const close = \(\) => \{ setOpen\(false\); btn\.current\?\.focus\(\); \};/);
  assert.equal((acct.match(/close\(\); (onResetDemo|previewSession)\(/g) ?? []).length, 3, "reset and both session previews close through it");
});

test("A15 every service error is the shared RefusalNote: composer, export row, late work and the boot screen (Q-02.03 M8)", () => {
  const ask = read("src/components/Ask.tsx");
  assert.match(ask, /export function RefusalNote\(\{ refusal, id, compact = false, alert = true \}/);
  assert.match(ask, /const advice = refusal\.advice \?\? remedyFor\(refusal\.code\)\.advice;/);
  const inv = read("src/components/Investigation.tsx");
  const composer = inv.slice(inv.indexOf("function Composer"), inv.indexOf("function FailedRun"));
  assert.match(composer, /\{err && <div className="composer-err"><RefusalNote refusal=\{err\} compact \/><\/div>\}/);
  // the box still names its note, and the alert is not inside the status (announced once)
  assert.match(composer, /<div id="composer-err">\s*<div role="status">\{note && !err && \(/);
  assert.ok(!/<code className="mono-s">\{err\.code\}/.test(composer), "no composer-only error markup");
  const ret = read("src/components/Retention.tsx");
  assert.match(ret, /\{state\?\.status === "failed" && <RefusalNote refusal=\{state\.refusal\} compact \/>\}/);
  assert.match(ret, /status: "failed", refusal: refusalOf\(e\)/);
  assert.ok(!/Not saved\./.test(ret));
  const late = read("src/components/Late.tsx");
  // the code with its remedy (RefusalNote calls remedyFor), not the bare code
  assert.match(late, /<RefusalNote refusal=\{\{ code: w\.error, message: "" \}\} compact alert=\{false\} \/>/);
  assert.ok(!/<code className="sub-line mono-s">\{w\.error\}/.test(late));
  const main = read("src/main.tsx");
  assert.match(main, /<RefusalNote refusal=\{\{ code: boot\.error\.code, message: boot\.error\.safe_message \}\} \/>/);
  assert.match(main, /role=\{boot\.status === "error" \? undefined : "status"\}/);
  // no other component formats a ProductError itself
  for (const [f, src] of components) {
    if (f === "Ask.tsx") continue;
    assert.ok(!/safe_message/.test(src), `${f} goes through refusalOf / RefusalNote`);
  }
  assert.match(read("src/components/Connect.tsx"), /<RefusalNote refusal=\{refusal\} id="scan-refusal" \/>/);
});

test("A16 one Refresh: Project, Start and Connect share the button, and the outcome is said in a status already in the page (Q-02.03 M7, M14)", () => {
  const ask = read("src/components/Ask.tsx");
  assert.match(ask, /aria-disabled=\{r\.busy \|\| undefined\} aria-busy=\{r\.busy\} onClick=\{\(\) => r\.run\(\)\}/);
  // hosted opens the ZIP picker from the same focused button; a busy press is guarded, not disabled
  assert.match(ask, /aria-disabled=\{r\.busy \|\| undefined\} aria-busy=\{r\.busy\}\s*onClick=\{\(\) => \{ if \(!r\.busy\) picker\.current\?\.click\(\); \}\}/);
  assert.match(ask, /\{r\.busy \? "Refreshing…" : hostedMode \? "Refresh from ZIP" : "Refresh"\}/);
  assert.match(ask, /const run = \(replacement\?: Blob\) => \{\s*if \(busy \|\| \(hostedMode && !replacement\)\) return;/);
  // the region is rendered before there is an outcome; only its content changes
  assert.match(ask, /<div role="status">\s*\{r\.outcome && \(/);
  assert.match(ask, /\{fresh \? "New snapshot" : "Unchanged"\}/);
  for (const f of ["Project.tsx", "Start.tsx", "Connect.tsx"]) {
    const src = read(`src/components/${f}`);
    assert.match(src, /= useRefresh\(/, f);
    assert.match(src, /<RefreshButton r=\{refresh\}/, f);
    assert.match(src, /<RefreshStatus r=\{refresh\}/, f);
    assert.ok(!/port\.refresh\(/.test(src), `${f} has no refresh of its own`);
  }
});

test("A17 status regions are always in the page and only their text changes (Q-02.03 M14)", () => {
  const inv = read("src/components/Investigation.tsx");
  assert.match(inv, /<span className="shown" role="status">\{shown\.length !== v\.claims\.length && `/);
  assert.match(read("src/components/Ask.tsx"), /<p className="fld-error" id=\{noteId\} role="status">\{note && </);
  assert.match(read("src/components/Retention.tsx"), /<span className="sub-line" role="status">\{state\?\.status === "saved" && /);
  assert.match(read("src/components/Connect.tsx"), /<div role="status">\{rescanned && </);
});

test("A18 a button that goes away on click does not drop focus to the page (Q-02.03 L10)", () => {
  const lg = read("src/components/Lineage.tsx");
  // one button for both views of a large graph, so it keeps focus
  assert.match(lg, /onClick=\{\(\) => setWhole\(w => !w\)\}>\{whole \? <>Show this claim&rsquo;s path<\/> : "Show all stages"\}/);
  assert.ok(!/setWhole\(true\)|setWhole\(false\)/.test(lg));
  // the last "Show 50 more" hands focus to the first node it revealed
  assert.match(lg, /if \(cap \+ 50 >= all\.length\) reveal\.current = all\[cap\]\.id;/);
  assert.match(lg, /ref=\{n\.id === reveal\.current \? focusRevealed : undefined\}/);
  const late = read("src/components/Late.tsx");
  // Dismiss names what it dismisses, and focus goes to the next row, else the previous, else the screen
  assert.match(late, /aria-label=\{`Dismiss: \$\{what\}/);
  assert.match(late, /\(row\?\.nextElementSibling \?\? row\?\.previousElementSibling\)\?\.querySelector<HTMLElement>\("\[data-dismiss\]"\);\s*\(near \?\? document\.getElementById\("main"\)\)\?\.focus\(\);\s*dismissLate\(id\);/);
});

test("A19 going to another screen is a link with a real address, not a button (Q-02.03 L13)", () => {
  const nav = /<button[^>]*onClick=\{\(\) => go\("/;
  for (const f of ["Gated.tsx", "Start.tsx", "Topbar.tsx", "Project.tsx", "Investigation.tsx"]) {
    assert.ok(!nav.test(read(`src/components/${f}`)), `${f}: no button navigates`);
  }
  assert.match(read("src/components/Gated.tsx"), /<a className="btn btn--sm" style=\{\{ marginLeft: 8 \}\} href=\{hrefFor\("start"\)\}>Go to Start<\/a>/);
  assert.match(read("src/components/Start.tsx"), /<a className="btn btn--primary" href=\{hrefFor\("connect"\)\}>/);
  assert.match(read("src/components/Topbar.tsx"), /<a href=\{hrefFor\("project"\)\}>/);
  const connect = read("src/components/Connect.tsx");
  assert.ok(!/href="#\//.test(connect), "no hard-coded address");
  assert.match(connect, /<a className="btn" href=\{hrefFor\("start"\)\}>Go to Start<\/a>/);
});
