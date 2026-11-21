/* Serve the production benchmark with npm run bench:serve before running this check. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { launch } from "./e2e/cdp.mjs";

const BASE = process.env.BENCH_BASE ?? "http://127.0.0.1:5275/";

test("the benchmark verifies the ordered initial node window against the input graph", async () => {
  const browser = await launch({ port: 9365 });
  try {
    for (const [nodes, visible] of [[1000, 26], [20, 20]]) {
      const url = new URL(`bench.html?n=${nodes}&runs=2`, BASE);
      await browser.page.send("Page.navigate", { url: url.href });
      await browser.page.until(`window.__bench?.nodes === ${nodes}`, 8000);
      const report = await browser.page.eval("window.__bench");
      assert.equal(report.nodesDrawn, visible);
      assert.equal(report.expectedNodesDrawn, visible);
      assert.equal(report.deterministic, true);
      assert.equal(report.visibleNodeIdsMatch, true);
    }
  } finally {
    await browser.close();
  }
});
