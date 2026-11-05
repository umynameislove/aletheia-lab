/* Bundle budget (Q-15). Run after `npm run build:live`: `npm run budget`.
   Limits are the gzip size a person downloads, set from the measured build on 2026-11-05. Lowering a
   number is fine; raising one needs a reason. Not a performance SLO: it only stops silent growth. */

import { readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "dist", "assets");
const KB = 1000;
const LIMITS = { mainJs: 80 * KB, anyChunk: 25 * KB, css: 22 * KB };

const files = readdirSync(ROOT);
const gz = f => gzipSync(readFileSync(join(ROOT, f))).length;
const html = readFileSync(join(ROOT, "..", "index.html"), "utf8");
const main = /assets\/(index-[^"]+\.js)/.exec(html)?.[1];
const js = files.filter(f => f.endsWith(".js"));
const css = files.filter(f => f.endsWith(".css"));

const rows = [
  ["main JS", main, gz(main), LIMITS.mainJs],
  ...js.filter(f => f !== main).map(f => ["chunk", f, gz(f), LIMITS.anyChunk]),
  ...css.map(f => ["CSS", f, gz(f), LIMITS.css]),
];
let over = 0;
for (const [kind, file, size, limit] of rows) {
  const ok = size <= limit;
  if (!ok) over += 1;
  console.log(`${ok ? "ok  " : "OVER"} ${kind.padEnd(8)} ${(size / KB).toFixed(1).padStart(6)} kB gzip  limit ${(limit / KB).toFixed(0)}  ${file}`);
}
if (over) {
  console.error(`${over} file(s) over budget`);
  process.exit(1);
}
