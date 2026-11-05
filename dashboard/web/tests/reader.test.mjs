/* What a source is to a claim, bounded lists, number formatting, the claim's lineage path and the
   contrast of the design tokens. All of these are pure, so they run here; layout is checked in a
   browser.   Run: npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { claimsCiting, readingFor, referenceText, relationOf, sourcesOf } from "../src/data/relation.ts";
import { pageOf, pageOfIndex } from "../src/data/paging.ts";
import { formatNumber, formatSigned } from "../src/data/format.ts";
import { claimPath } from "../src/data/subgraph.ts";
import { syntheticGraph } from "../src/data/syntheticGraph.ts";
import { localCapabilities } from "../src/service/capabilities.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = p => readFileSync(join(ROOT, p), "utf8");
const DEMO = JSON.parse(read("src/data/synthetic_p6_view.json"));

/* Valid under the schema: B is visible to the turn, and the claim does not cite it. C is against the claim. */
function fixture() {
  const v = structuredClone(DEMO);
  const base = v.evidence[0];
  v.evidence.push({ ...base, id: "evidence-B", title: "Unrelated context" }, { ...base, id: "evidence-C", title: "Against" }, { ...base, id: "evidence-hidden-from-turn", title: "Not seen" });
  v.conversation.turns[0].visible_evidence_ids.push("evidence-B", "evidence-C");
  v.claims[0].counterevidence_ids = ["evidence-C"];
  return { v, claim: v.claims[0], cited: base.id };
}

test("C1 being in the view is not being cited: claim A + evidence B is context", () => {
  const { v, claim } = fixture();
  assert.equal(relationOf(v, claim, "evidence-B"), "context");
  const r = readingFor(v, claim, "evidence-B");
  assert.equal(r.evidence.id, "evidence-B");
  assert.equal(r.relation, "context");
});

test("C2 only citation_ids makes something cited; counterevidence never reads as support", () => {
  const { v, claim, cited } = fixture();
  assert.equal(relationOf(v, claim, cited), "cited");
  assert.equal(relationOf(v, claim, "evidence-C"), "counter");
  claim.citation_ids.push("evidence-C");
  assert.equal(relationOf(v, claim, "evidence-C"), "counter", "listed as both: the opposing role wins");
});

test("C3 outside the turn's sight and uncited is no relation at all", () => {
  const { v, claim } = fixture();
  assert.equal(relationOf(v, claim, "evidence-hidden-from-turn"), "unrelated");
});

test("C4 a linked source the result does not contain is reported, and nothing replaces it", () => {
  const { v, claim } = fixture();
  assert.deepEqual(readingFor(v, claim, "evidence-from-another-result"), { evidence: null, relation: "absent", missingId: "evidence-from-another-result" });
});

test("C5 with no link the reader opens the claim's own first source, support before opposition", () => {
  const { v, claim, cited } = fixture();
  assert.equal(readingFor(v, claim, null).evidence.id, cited);
  claim.citation_ids = [];
  assert.equal(readingFor(v, claim, null).evidence.id, "evidence-C");
  assert.equal(readingFor(v, claim, null).relation, "counter");
  claim.counterevidence_ids = [];
  assert.deepEqual(readingFor(v, claim, null), { evidence: null, relation: null, missingId: null });
});

test("C6 no claim selected: a source is related to nothing", () => {
  const { v, cited } = fixture();
  assert.equal(relationOf(v, null, cited), "unrelated");
  assert.equal(claimsCiting(v, cited).length, 1);
  assert.equal(claimsCiting(v, "evidence-B").length, 0);
});

test("C7 every cited and opposing source is listed, in its own group", () => {
  const { v, claim, cited } = fixture();
  claim.citation_ids.push("evidence-B");
  const s = sourcesOf(v, claim);
  assert.deepEqual(s.cited.map(e => e.id), [cited, "evidence-B"]);
  assert.deepEqual(s.counter.map(e => e.id), ["evidence-C"]);
});

test("C8 a copied reference names the record and hash, and no path", () => {
  const { v } = fixture();
  const text = referenceText(v.evidence[0]);
  assert.match(text, /demo-comparison-001/);
  assert.match(text, /sha256 40c9ff8c/);
  assert.ok(!/[/\\\\]Users|[A-Z]:\\\\/.test(text));
});

test("C9 the old label cannot come back: no screen calls everything cited evidence", () => {
  for (const f of readdirSync(join(ROOT, "src/components"))) {
    assert.ok(!read(`src/components/${f}`).includes("Cited evidence"), f);
  }
});

test("P1 every row is reachable and no page holds more than the limit", () => {
  const rows = Array.from({ length: 10_000 }, (_, i) => i);
  const seen = [];
  for (let p = 1; p <= pageOf(rows, 1).pages; p += 1) {
    const page = pageOf(rows, p);
    assert.ok(page.rows.length <= 50);
    seen.push(...page.rows);
  }
  assert.deepEqual(seen, rows);
  assert.equal(pageOf(rows, 1).pages, 200);
});

test("P2 a stale page number lands on a real page, not an empty list", () => {
  const rows = [1, 2, 3];
  assert.deepEqual(pageOf(rows, 99).rows, [1, 2, 3]);
  assert.deepEqual(pageOf(rows, -4).rows, [1, 2, 3]);
  assert.deepEqual(pageOf(rows, NaN).rows, [1, 2, 3]);
  const none = pageOf([], 1);
  assert.deepEqual([none.first, none.last, none.total, none.pages], [0, 0, 0, 1]);
});

test("P3 keyboard moves find the page a row is on", () => {
  assert.deepEqual([0, 49, 50, 9_999].map(i => pageOfIndex(i)), [1, 1, 2, 200]);
});

test("N1 formatting changes separators only, never digits", () => {
  assert.equal(formatNumber(0.74, "en"), "0.74");
  assert.equal(formatNumber(0.74, "vi"), "0,74");
  assert.equal(formatNumber(1234567.5, "en"), "1,234,567.5");
  assert.equal(formatNumber(0.123456789012, "en"), "0.123456789012");
  assert.equal(formatNumber(0, "en"), "0");
});

test("N2 tiny and huge values keep their exact form instead of rounding to 0", () => {
  assert.equal(formatNumber(1e-12, "en"), "1e-12");
  assert.equal(formatNumber(-3.5e-9, "en"), "-3.5e-9");
  assert.equal(formatNumber(1e21, "en"), "1e+21");
});

test("N3 the sign stays visible on a change", () => {
  assert.equal(formatSigned(0.2, "en"), "+0.2");
  assert.equal(formatSigned(-0.08, "en"), "-0.08");
  assert.equal(formatSigned(0, "en"), "0");
});

test("G1 a claim's path follows real edges and does not pick up its neighbours' evidence", () => {
  const g = syntheticGraph(1000);
  const claimId = g.claimIds[0];
  const path = claimPath(g.nodes, g.edges, claimId);
  const nodeIds = new Set(path.nodes.map(n => n.id));
  const claims = path.nodes.filter(n => n.kind === "AtomicClaim");
  assert.deepEqual(claims.map(c => c.source_id), [claimId], "one claim only");
  assert.ok(path.nodes.length < g.nodes.length / 2, "smaller than the graph");
  const known = new Set(g.edges.map(e => e.id));
  for (const e of path.edges) {
    assert.ok(known.has(e.id), "no invented edge");
    assert.ok(nodeIds.has(e.source) && nodeIds.has(e.target), "no dangling edge");
  }
});

test("G2 an unknown claim has an empty path", () => {
  const g = syntheticGraph(20);
  assert.deepEqual(claimPath(g.nodes, g.edges, "no-such-claim"), { nodes: [], edges: [] });
});

test("K1 capabilities come from the running port", () => {
  assert.equal(localCapabilities("live").deployment_mode, "local");
  assert.ok(localCapabilities("live").supported_export_formats.includes("pdf"));
  assert.ok(!localCapabilities("demo").supported_export_formats.includes("pdf"));
  assert.deepEqual(localCapabilities("demo").supported_source_types, [], "the demo reads no real folder");
});

/* ---- WCAG contrast, measured from tokens.css ---- */
const CSS = read("src/styles/tokens.css");
function tokens(theme) {
  const re = theme === "light" ? /:root\[data-theme="light"\] \{([\s\S]*?)\n\}/ : /:root,\n:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/;
  const out = {};
  for (const m of re.exec(CSS)[1].matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) out[m[1]] = m[2];
  return out;
}
const channel = c => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const lum = hex => { const n = parseInt(hex.slice(1), 16); return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255); };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

for (const theme of ["light", "dark"]) {
  test(`T1 ${theme}: text is at least 4.5:1 on every surface`, () => {
    const t = tokens(theme);
    for (const fg of ["ink", "ink-2", "ink-3"]) {
      for (const bg of ["bg-app", "bg-rail", "bg-1", "bg-2", "bg-3", "bg-input", "bg-hover", "bg-insp"]) {
        assert.ok(ratio(t[fg], t[bg]) >= 4.5, `${theme} ${fg} on ${bg}: ${ratio(t[fg], t[bg]).toFixed(2)}`);
      }
    }
  });

  test(`T2 ${theme}: the action colour and state marks are readable (3:1 for non-text)`, () => {
    const t = tokens(theme);
    assert.ok(ratio(t["on-accent"], t["accent-fill"]) >= 4.5, "primary button text");
    assert.ok(ratio(t["on-accent"], t["accent-fill-hover"]) >= 4.5, "primary button hover");
    for (const bg of ["bg-app", "bg-1", "bg-2", "bg-input"]) assert.ok(ratio(t.accent, bg === "x" ? t.bg1 : t[bg]) >= 4.5, `accent as text on ${bg}`);
    for (const mark of ["ok", "partial", "no", "contra", "tick"]) {
      for (const bg of ["bg-app", "bg-1", "bg-2", "bg-insp"]) assert.ok(ratio(t[mark], t[bg]) >= 3, `${theme} ${mark} on ${bg}: ${ratio(t[mark], t[bg]).toFixed(2)}`);
    }
  });

  test(`T3 ${theme}: action and state colours are different colours`, () => {
    const t = tokens(theme);
    for (const mark of ["ok", "partial", "no", "contra", "tick"]) assert.notEqual(t.accent.toLowerCase(), t[mark].toLowerCase());
  });
}

/* ---- report file names ---- */
import { reportFilename } from "../src/data/format.ts";

test("E1 a report's file name says which project and result, in plain ASCII", () => {
  const id = "p6-result-7ff78b3669c373b04c0217d4f2748f6321eced25a6dc41f559d788e981eec52c";
  assert.equal(reportFilename("Imported project", id, "json"), "aletheia-imported-project-7ff78b36.json");
  assert.equal(reportFilename("Dự án Đánh giá", id, "pdf"), "aletheia-du-an-danh-gia-7ff78b36.pdf");
  assert.equal(reportFilename("Dự án Đánh giá".normalize("NFD"), id, "pdf"), "aletheia-du-an-danh-gia-7ff78b36.pdf", "NFC and NFD give the same name");
});

test("E2 a name can never be a path, and an empty one still works", () => {
  for (const hostile of ["../../etc/passwd", "a/b\\c", "C:\\Users\\x", "..", "name\u0000null"]) {
    const name = reportFilename(hostile, "demo-result-001", "md");
    assert.ok(/^[a-z0-9.-]+$/.test(name), name);
    assert.ok(!name.includes(".."), name);
  }
  assert.equal(reportFilename("日本語", "demo-result-001", "md"), "aletheia-project-demo-result-001.md");
  assert.ok(reportFilename("x".repeat(500), "r", "md").length < 80, "bounded");
});
