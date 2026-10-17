/* Accessibility guards (Q12) that can be checked from source. The browser audit — contrast,
   390px and 200% zoom reflow, keyboard order — is recorded in docs/p6-dashboard-quickstart.md.
   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

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
  assert.match(modal, /return \(\) => prev\?\.focus\?\.\(\)/);
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
