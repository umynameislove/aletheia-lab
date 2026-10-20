/* UI contract test (React app) for the shared preview_import fixture.   Run: npm test
   The content rules (severity per list, withheld/ambiguous cases, path hygiene …) live in
   the prototype tests outside this repository — they run on the same bytes, which A1 proves. */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const LOCKED = {
  v1: { file: "p6_preview_import_v1.json", sha: "ac3a30a3c866729e24e233e7cd45738103bb035a0637992d377a43025b3e9ccc" },
  v2: { file: "p6_preview_import_v2.json", sha: "8e04f2e3b6fb854c8a22e79b0f47fef1663191df146730337b1a6b8434aad4c6" },
};
// the version is whatever src/data/preview.ts actually imports — not whatever happens to be on disk
const PREVIEW_TS = readFileSync(join(ROOT, "src/data/preview.ts"), "utf8");
const VERSION = PREVIEW_TS.includes(`"./${LOCKED.v2.file}"`) ? "v2" : "v1";
const LOCKED_SHA = LOCKED[VERSION].sha;
const raw = readFileSync(join(ROOT, "src/data", LOCKED[VERSION].file));
const v2OnDisk = existsSync(join(ROOT, "src/data", LOCKED.v2.file));
const TSC = join(ROOT, "node_modules/typescript/bin/tsc");

function typecheck(typesFile, importName, version = VERSION) {
  const dir = mkdtempSync(join(tmpdir(), "p6-app-contract-"));
  try {
    writeFileSync(join(dir, "types.ts"), readFileSync(typesFile));
    // v1 predates metric_name_candidates: checked against the contract minus that one field
    const header = version === "v2"
      ? `import type { PreviewImportResult as T } from "./${importName}";\n`
      : `import type { PreviewImportResult, MappingCandidates, MetricSourceCandidate } from "./${importName}";\n` +
        `type T = Omit<PreviewImportResult, "mapping_candidates"> & { mapping_candidates: Omit<MappingCandidates, "metric_sources"> & { metric_sources: Omit<MetricSourceCandidate, "metric_name_candidates">[] } };\n`;
    writeFileSync(join(dir, "check.ts"), `${header}export const fixture: T = ${raw.toString("utf8")};\n`);
    execFileSync(process.execPath, [TSC, "--noEmit", "--strict", "--target", "ES2020", "--moduleResolution", "bundler",
      "--module", "ESNext", join(dir, "check.ts")], { stdio: "pipe" });
  } catch (e) {
    assert.fail("tsc rejected the fixture:\n" + (e.stdout?.toString() || e.message));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test(`A1 the React app reads the service-locked fixture, byte for byte (${VERSION})`, () => {
  assert.equal(createHash("sha256").update(raw).digest("hex"), LOCKED_SHA);
});

test("A2 the app is on the v2 fixture (K04d)", { skip: VERSION === "v2" ? false : v2OnDisk ? "v2 is on disk but src/data/preview.ts still imports v1" : "waiting for p6_preview_import_v2.json (SHA 8e04f2e3…) — not delivered yet" }, () => {
  assert.equal(VERSION, "v2");
});

test("B1 fixture satisfies the app's own PreviewImportResult (src/types.ts)", () => {
  typecheck(join(ROOT, "src/types.ts"), "types");
});

test("B2 fixture satisfies the contract handed to the service owner (product-view-additions.d.ts)", () => {
  typecheck(join(ROOT, "contract/product-view-additions.d.ts"), "types");
});

test("D1 Connect reads preview_import through the port and holds no hand-written preview data", () => {
  const preview = readFileSync(join(ROOT, "src/data/preview.ts"), "utf8");
  assert.ok(preview.includes(`from "./${LOCKED[VERSION].file}"`), `preview.ts does not import ${LOCKED[VERSION].file}`);
  const src = readFileSync(join(ROOT, "src/components/Connect.tsx"), "utf8");
  assert.ok(!/from "\.\.\/data\//.test(src), "Connect must not import fixtures directly — only through the port");
  assert.match(src, /await port\.preview_import\(/, "Scan must call port.preview_import");
  assert.match(src, /await port\.confirm_import\(preview\.preview_id, confirmPayload\(/, "Confirm must call port.confirm_import with the built payload");
  // every place a preview value is shown is pinned to the port's answer, so one hand-typed number fails
  const pins = [
    [/\["Included", preview\.included_count,/, "Included tile"],
    [/\["Redacted", preview\.redacted_count,/, "Redacted tile"],
    [/\["Excluded", preview\.excluded_count,/, "Excluded tile"],
    [/\["Withheld", preview\.withheld_count,/, "Withheld tile"],
    [/\$\{p\.included_count\} included, \$\{p\.redacted_count\} redacted, \$\{p\.excluded_count\} excluded, \$\{p\.withheld_count\} withheld/, "scan toast"],
    [/const issues = \[\.\.\.preview\.blockers, \.\.\.preview\.warnings\]/, "issue table + severity counts"],
    [/const ws = preview\.warnings;/, "warning summary"],
    [/preview\.outbound_categories\.length/, "outbound categories"],
    [/C\.targets\.map\(/, "target picker"],
    [/C\.metric_sources\.map\(/, "metric source picker"],
    [/C\.runs\.map\(/, "run pickers"],
    [/m\?\.metric_name_candidates \?\? \[\]\)\.map\(name =>/, "metric definition rows"],
    [/const offered = ms\?\.metric_name_candidates \?\? \[\];/, "manual-entry decision"],
    [/metric_definitions: definitionsPayload\(m\.defs\)/, "payload carries metric_definitions"],
    // the exact confirm_import mapping agreed on 2026-10-05: IDs and format verbatim from the candidates
    [/target: \{ mapping_id: t\?\.mapping_id \?\? "", project_item_id: m\.target, target_field: m\.targetLabel, identifier_field: m\.targetField \}/, "target mapping"],
    [/mapping_id: ms\?\.mapping_id \?\? "", project_item_id: m\.metric, format: ms\?\.format/, "metric source mapping_id + format"],
    [/step_field: m\.stepField \|\| null/, "step_field null when unset"],
    // tracked runs, each once; baseline_run_id only designates the reference run among them (Kiên, 2026-10-08)
    [/runs: runMappings\(m\)/, "tracked runs and their configs use the executable run-mapping helper"],
    [/baseline_run_id: m\.baseline,/, "baseline_run_id names the reference run"],
    [/if \(!ms \|\| ms\.format === "csv"\) return \[\];/, "records_path [] for CSV, never null"],
  ];
  for (const [re, what] of pins) assert.match(src, re, `${what} is not read from the preview`);
  for (const stale of ["p3-item-", "pi-dataset", "pi-metrics", "dataset/main.csv", "metrics/validation.json", "source_read_failed", "12 included"])
    assert.ok(!src.includes(stale), `hand-written fixture value in Connect.tsx: ${stale}`);
});

test("D2 metric definitions have no defaults and mirror every mapping_invalid rule", () => {
  const src = readFileSync(join(ROOT, "src/components/Connect.tsx"), "utf8");
  assert.match(src, /direction: "" as const, threshold: ""/, "a definition row must start with no direction and no threshold");
  for (const [re, what] of [
    [/"Every metric has a direction and a threshold", m\.defs\.length > 0/, "missing or empty definitions"],
    [/"Thresholds are finite and ≥ 0"/, "negative or non-finite threshold"],
    [/"Metric names are unique"/, "duplicate metric names"],
  ]) assert.match(src, re, what);
  const re = /const THRESHOLD = (\/.*\/);/.exec(src);
  assert.ok(re, "threshold pattern not found");
  const THRESHOLD = eval(re[1]);
  const ok = t => THRESHOLD.test(t.trim()) && Number.isFinite(Number(t.trim()));
  // mirrors the backend exactly: finite and >= 0 — scientific notation is finite (Kiên, K04d)
  for (const t of ["0", "0.05", "0.08", ".5", "10", "0.", "1e3", "1E3", "2.5e-4", " 0.2 "]) assert.ok(ok(t), `should accept ${JSON.stringify(t)}`);
  for (const t of ["-0.1", "-1e3", "Infinity", "-Infinity", "NaN", "1e999", "", "abc", "0x10", "1e"]) assert.ok(!ok(t), `should reject ${JSON.stringify(t)}`);
});

test("D3 runs are labelled as tracked and reference runs — never as two runs compared with each other", () => {
  const src = readFileSync(join(ROOT, "src/components/Connect.tsx"), "utf8");
  for (const stale of ["Run under audit", "Baseline run", "Baseline names a different run"])
    assert.ok(!src.includes(stale), `stale run wording in Connect.tsx: ${stale}`);
  assert.match(src, /aria-label="Tracked runs"/);
  assert.match(src, /label="Reference run" code="baseline_run_id"/);
  assert.match(src, /Metric changes are calculated per run across snapshots, not between two runs\./);
  // a run with observations must be declared; a declared run must have observations (Kiên, 2026-10-08)
  assert.match(src, /"Every run with metrics in this source is declared"/);
  assert.match(src, /"Every declared run has metrics in this source"/);
  assert.ok(!src.includes("it may be the tracked run itself"), "stale claim that the reference run may equal the tracked run");
  // block only where source_metric_item_ids is precise: CSV, or JSON with one records path and run field (Kiên, 2026-10-09)
  assert.match(src, /const precise = !!ms && \(ms\.format === "csv" \|\| \(ms\.records_path_candidates\.length <= 1 && ms\.run_id_field_candidates\.length <= 1\)\);/);
  assert.equal((src.match(/"mapping_invalid", !precise\]/g) || []).length, 2, "both run checks report mapping_invalid");
  // unknown_run / run_without_metrics are internal P3 codes; the public boundary only says mapping_invalid
  assert.ok(!/unknown_run|run_without_metrics/.test(readFileSync(join(ROOT, "src/service/errors.ts"), "utf8")));
  assert.match(src, /const failing = res\.filter\(r => !r\[1\] && !r\[3\]\);/, "warn-only checks never block Confirm");
});
