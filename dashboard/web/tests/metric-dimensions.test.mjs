import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { checkProductView } from "../src/service/contract.ts";
import { createHostedClient } from "../src/service/hosted-client.ts";
import { definitionWords, formatNumber, metricDimensions } from "../src/data/format.ts";

// This immutable fixture is produced by the actual ProductService import/refresh/analysis path.
const fixture = JSON.parse(readFileSync(fileURLToPath(new URL("../../../fixtures/metric-dimensions-v2/product-view.json", import.meta.url)), "utf8"));
const legacy = JSON.parse(readFileSync(fileURLToPath(new URL("../src/data/synthetic_p6_view.json", import.meta.url)), "utf8"));
const invalid = { code: "response_invalid" };

test("the actual v2 producer view retains its class, source unit, operands and inclusive threshold", () => {
  assert.equal(checkProductView(fixture), fixture);
  const change = fixture.snapshot.metric_changes[0];
  assert.equal(change.after.class_label, "dương tính"); assert.equal(change.after.unit, "%");
  assert.equal(change.after.unit_origin, "source"); assert.equal(change.delta, -0.1);
  assert.equal(change.adverse_status, "adverse"); assert.equal(fixture.snapshot.metric_definitions[0].regression_threshold, 0.1);
  assert.match(metricDimensions(change), /Class: dương tính.*Unit: % \(from source\)/);
  assert.match(definitionWords(fixture.snapshot.metric_definitions[0]), /unit %/);
  assert.equal(formatNumber(change.after.value, "en"), "0.7", "a % label does not multiply the source value");
});

test("v2 dimensions are required and source/declared/unknown unit provenance is checked", () => {
  for (const key of ["class_label", "unit", "unit_origin"]) {
    const value = structuredClone(fixture); delete value.snapshot.metric_changes[0].after[key];
    assert.throws(() => checkProductView(value), invalid, key);
  }
  const missing = structuredClone(fixture); delete missing.snapshot.metric_definitions[0].unit;
  assert.throws(() => checkProductView(missing), invalid);
  for (const origin of ["declared", "source"]) {
    const value = structuredClone(fixture);
    for (const observation of [value.snapshot.metric_changes[0].before, value.snapshot.metric_changes[0].after]) observation.unit_origin = origin;
    assert.equal(checkProductView(value), value);
  }
  const unknown = structuredClone(fixture); unknown.snapshot.metric_definitions[0].unit = null;
  for (const observation of [unknown.snapshot.metric_changes[0].before, unknown.snapshot.metric_changes[0].after]) {
    observation.unit = null; observation.unit_origin = "unknown";
  }
  assert.equal(checkProductView(unknown), unknown);
  assert.match(metricDimensions(unknown.snapshot.metric_changes[0]), /Unknown unit/);
  unknown.snapshot.metric_changes[0].after.unit_origin = "declared";
  assert.throws(() => checkProductView(unknown), invalid);
});

test("a unit mismatch or a different class cannot be displayed as one comparable change", () => {
  for (const patch of [{ unit: "ratio" }, { class_label: "âm tính" }, { class_label: "pa\u034Fssword" }, { unit: "x".repeat(65) }]) {
    const value = structuredClone(fixture); Object.assign(value.snapshot.metric_changes[0].after, patch);
    assert.throws(() => checkProductView(value), invalid);
  }
});

test("v1 stays readable and cannot carry undeclared v2 dimensions", () => {
  assert.equal(checkProductView(legacy), legacy);
  const disguised = structuredClone(fixture); disguised.schema_version = "p6-product-view/v1";
  assert.throws(() => checkProductView(disguised), invalid);
  const future = structuredClone(fixture); future.schema_version = "p6-product-view/v3";
  assert.throws(() => checkProductView(future), { code: "schema_unsupported" });
});

test("the hosted output boundary accepts the actual v2 producer view only for the requested scope", async () => {
  const session = { user_id: "actor", context_id: "ctx", csrf_token: "synthetic-csrf" };
  const client = createHostedClient({ session, isCurrent: () => true, fetch: async () => new Response(JSON.stringify({
    schema_version: "product-api/v1-draft", workspace_id: "ws", job_id: "job", operation: "analyze", result: fixture,
  }), { headers: { "Content-Type": "application/json" } }) });
  const result = await client.loadOutput("ws", "job", { operation: "analyze", snapshotId: fixture.snapshot.id, projectId: fixture.project.id });
  assert.deepEqual(result.result, fixture);
  await assert.rejects(client.loadOutput("ws", "job", { operation: "analyze", snapshotId: "wrong" }), { code: "response_out_of_scope" });
});
