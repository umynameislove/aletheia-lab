import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readSession } from "../src/service/hosted.ts";

const session = JSON.parse(readFileSync(new URL("./fixtures/j01-review-v5/session.json", import.meta.url), "utf8"));
test("workspace creation is false for legacy sessions and accepts only an explicit boolean", () => {
  assert.equal(readSession(session).can_create_workspace, false);
  for (const allowed of [false, true]) assert.equal(readSession({ ...session, can_create_workspace: allowed }).can_create_workspace, allowed);
  for (const value of [null, 0, 1, "true", "false"]) {
    assert.throws(() => readSession({ ...session, can_create_workspace: value }), e => e.code === "response_invalid");
  }
});
