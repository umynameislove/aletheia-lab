/* Consumer side of K-04: Kiên's workspace, member, invitation and grant payloads, unchanged, through the
   client's own readers. tests/fixtures/k04-v1-draft/ is a copy of fixtures/j01/k04-workspaces-v1-draft/
   from k04-review-20261110.zip. Each negative case starts from a producer payload and breaks one thing.
   Run: npm test
   Against another producer drop, unchanged file names: K04_FIXTURES=/path/to/k04-fixtures npm test */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { GRANT_ACTIONS, ifMatch, MEMBERS_MAX, readGrant, readInvitation, readMember, readMembers, readRevokedInvitation, readWorkspace, ROLES } from "../src/service/hosted.ts";

const DIR = process.env.K04_FIXTURES || join(dirname(fileURLToPath(import.meta.url)), "fixtures/k04-v1-draft");
const fixture = f => JSON.parse(readFileSync(join(DIR, f), "utf8"));
const WS = "ws_synthetic";
const code = c => e => e.code === c;
const schemas = fixture("openapi-workspaces.json").components.schemas;

test("W1 each producer payload is read as it is", () => {
  const w = readWorkspace(fixture("workspace.json"), WS);
  assert.deepEqual(w, { workspace_id: WS, workspace_name: "Workspace tổng hợp", role: "owner", revision: 1 });
  assert.equal(readWorkspace(fixture("workspace.json"), null).workspace_id, WS);
  assert.equal(readMember(fixture("member.json"), WS, "user_synthetic").revision, 2);
  const members = fixture("members.json");
  assert.deepEqual(readMembers(members, WS).map(member => member.user_id), members.items.map(member => member.user_id));
  const invitation = fixture("invitation.json");
  const inv = readInvitation(invitation, WS, invitation.user_id);
  assert.equal(inv.role, invitation.role);
  assert.deepEqual(readGrant(fixture("grant.json"), WS, "src_synthetic", "user_synthetic").actions, ["read", "write"]);
  assert.deepEqual(readRevokedInvitation({ schema_version: "product-api/v1-draft", workspace_id: WS, invitation_id: "inv_synthetic", revoked: true }, WS, "inv_synthetic"),
    { workspace_id: WS, invitation_id: "inv_synthetic" });
});

test("W2 an answer about another workspace, member, source or invitation is not used", () => {
  assert.throws(() => readWorkspace(fixture("workspace.json"), "ws_other"), code("response_out_of_scope"));
  assert.throws(() => readMember(fixture("member.json"), WS, "user_other"), code("response_out_of_scope"));
  assert.throws(() => readMembers({ ...fixture("members.json"), items: [{ ...fixture("member.json"), workspace_id: "ws_other" }] }, WS), code("response_out_of_scope"));
  assert.throws(() => readInvitation(fixture("invitation.json"), WS, "user_other"), code("response_out_of_scope"));
  assert.throws(() => readGrant(fixture("grant.json"), WS, "src_other", "user_synthetic"), code("response_out_of_scope"));
  assert.throws(() => readGrant(fixture("grant.json"), WS, "src_synthetic", "user_other"), code("response_out_of_scope"));
  assert.throws(() => readRevokedInvitation({ schema_version: "product-api/v1-draft", workspace_id: WS, invitation_id: "inv_x", revoked: true }, WS, "inv_synthetic"), code("response_out_of_scope"));
});

test("W3 a wrong version, a missing field or an unknown role fails closed", () => {
  assert.throws(() => readWorkspace({ ...fixture("workspace.json"), schema_version: "product-api/v1" }, WS), code("schema_unsupported"));
  for (const f of ["workspace_name", "membership"]) {
    const w = fixture("workspace.json"); delete w[f];
    assert.throws(() => readWorkspace(w, WS), code("response_invalid"), f);
  }
  assert.throws(() => readWorkspace({ ...fixture("workspace.json"), membership: { role: "superuser", revision: 1 } }, WS), code("response_invalid"));
  assert.throws(() => readMember({ ...fixture("member.json"), role: "Owner" }, WS), code("response_invalid"));
  assert.throws(() => readMember({ ...fixture("member.json"), active: "true" }, WS), code("response_invalid"));
  assert.throws(() => readMember({ ...fixture("member.json"), revision: -1 }, WS), code("response_invalid"));
  assert.throws(() => readMember({ ...fixture("member.json"), revision: 1.5 }, WS), code("response_invalid"));
});

test("W4 a member list over the draft limit, or naming a person twice, is an error, never cut", () => {
  const m = fixture("member.json");
  const many = Array.from({ length: MEMBERS_MAX + 1 }, (_, i) => ({ ...m, user_id: `u${i}` }));
  assert.throws(() => readMembers({ ...fixture("members.json"), items: many }, WS), code("response_invalid"));
  assert.equal(readMembers({ ...fixture("members.json"), items: many.slice(0, MEMBERS_MAX) }, WS).length, MEMBERS_MAX);
  assert.throws(() => readMembers({ ...fixture("members.json"), items: [m, m] }, WS), code("response_invalid"));
});

test("W5 an invitation never makes an owner, and its expiry and token are checked", () => {
  const inv = fixture("invitation.json");
  assert.throws(() => readInvitation({ ...inv, role: "owner" }, WS, inv.user_id), code("response_invalid"));
  assert.throws(() => readInvitation({ ...inv, expires_at: "2026-02-30T00:00:00Z" }, WS, inv.user_id), code("response_invalid"));
  assert.throws(() => readInvitation({ ...inv, token: "x".repeat(42) }, WS, inv.user_id), code("response_invalid"));
  assert.throws(() => readInvitation({ ...inv, token: "x".repeat(129) }, WS, inv.user_id), code("response_invalid"));
  assert.throws(() => readInvitation({ ...inv, token: "x".repeat(42) + " " }, WS, inv.user_id), code("response_invalid"));
});

test("W6 a grant holds read unless it is empty, each action once, from the agreed list", () => {
  const g = fixture("grant.json");
  assert.deepEqual(readGrant({ ...g, actions: [] }, WS, "src_synthetic", "user_synthetic").actions, []);
  for (const actions of [["write"], ["read", "read"], ["read", "admin"]])
    assert.throws(() => readGrant({ ...g, actions }, WS, "src_synthetic", "user_synthetic"), code("response_invalid"), actions.join());
});

test("W7 the client's roles and actions are the producer's, and If-Match is the quoted revision", () => {
  assert.deepEqual([...ROLES], schemas.Member.properties.role.enum);
  assert.deepEqual([...ROLES].filter(r => r !== "owner"), schemas.Invitation.properties.role.enum);
  assert.deepEqual([...GRANT_ACTIONS], schemas.SourceGrant.properties.actions.items.enum);
  const pattern = new RegExp(fixture("openapi-workspaces.json").paths["/v1/workspaces/{workspace_id}/members/{user_id}"].patch
    .parameters.find(p => p.name === "If-Match").schema.pattern);
  for (const r of [0, 2, 1234567890]) assert.match(ifMatch(r), pattern);
});
