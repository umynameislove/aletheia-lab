# K-03 hosted browser preflight

## Current API-copy handoff — macOS, 2026-11-11

The hosted entry is now mounted: server session/workspaces, HostedConnect,
renew/logout/logout-all, request/context fences and the visible monotonic
four-second authority lease are wired. The final controlled-transport Chrome
checks are separate from actual IdP login. Current local evidence is maintained
outside this source copy under `Aletheia-Kien-API/Bang-chung/20261111-completion`.
The Windows counts, paths and "unmounted" observations below are historical;
they do not describe the current API-copy UI.

### Additional backend qualification without IdP — 2026-11-11

Session renewal now preserves the current `can_create_workspace` hint using
the same server policy as GET session. The policy lookup runs before credential
rotation, so a lookup failure or timeout does not rotate away the client's
working credential. The hint remains an affordance; workspace creation still
checks current authority independently. Early renewals and rotations, policy
changes, rejected policy output and failure paths have focused offline tests.

`tests/integration/test_k03_session_races_postgres.py` now exercises the real
session/CSRF routes with native PostgreSQL and two independent restricted API
pools. Twelve renewals held before their DB writes yield one rotation and eleven
conflicts; the old credential is denied and early renewal does not rotate again.
Both commit orders of renewal versus logout-all revoke both same-account contexts,
including a newly issued cookie, while another account remains valid. These
three cases use server-seeded sessions and no provider/discovery service. They
qualify the backend boundary; browser cookies, IdP login and joint account UX
retain the separate checklist below. Evidence is in
`Aletheia-Kien-API/Bang-chung/20261111-kien-remaining/`.

Owner instructions for this handoff: skip every Docker-dependent run. The
owner will test the actual IdP/staging flow tonight; it is not counted passed
here. No Docker Desktop, containers, global trust store, production account or
provider policy was changed for this preflight. The host has 8 GiB RAM and Desktop
is configured for 4 GiB, so its recovery fixture was not started during the
independent native PostgreSQL storage test.

### Tonight's real-IdP receiving checklist (no Docker prerequisite)

Use an approved, already available test/staging IdP and HTTPS application origin.
The repository's `configs/hosted/staging.example.toml` is a template, not a live
configuration. Keep issuer/client/redirect/secret/CA values in the operator's
private configuration; do not paste tokens, codes, passwords or secrets into chat.
Build hosted assets for the same release/origin; optional IdP logout navigation
also requires the approved `VITE_IDP_ORIGIN`. The application uses the server's
approved default return route, rather than guessing an arbitrary hash return.

1. On a test account, go through sign-in/callback. Check the expected issuer,
   subject/account and workspace, server session200, and Secure/HttpOnly session
   cookie behavior. A401 must show signed-out/recovery, with no demo substitution.
2. Use the owner-approved A/B accounts in separate tabs. Switch account/workspace
   and navigate history/reload while an old response is pending. Old data/answers
   must not replace the current scope. Check visible-tab revocation/cover and
   BFCache/resume; do not infer a wall-clock guarantee for OS-throttled hidden tabs.
3. Sign out, sign out all app sessions, renew and expire a session. Confirm old
   app credentials stop working and another permitted context behaves according
   to policy. Application logout is not a claim that every external IdP SSO
   session has been revoked.
4. Run only the operator-approved recovery/MFA cases. Password reset keeps the
   original factor; lost-factor/production assurance policy belongs to the IdP
   owner. The trusted manual revoke-sessions CLI is documented in
   `k03-account-recovery-runbook.md`; its help and private identity-file format
   are canonical. Do not test this against an unrelated or production account.
5. Record release/source and contract hashes, approved origin/IdP, timestamps,
   expected versus observed safe outcomes and consumer evidence in the existing
   K03/J02 tracking rows. Provider tokens and callback codes are not evidence
   to publish. Keep real-IdP, seeded-session native HTTPS and controlled transport
   results as separate scopes.

## Historical checkpoints before this API-copy handoff


## Final local proof update — 2026-11-11 09:06 +07

**HostedReady=false; actual hosted browser/joint acceptance is still open.**
The existing canonical 28-JSON/20-payload inventory below remains the producer
input; no frontend, contract, build, TLS origin or CA was changed here.

Independent K-03 local proof is now qualified: final auth component **607 tests**
over **135 actual dependency inputs**, no used/outside hash drift; separate
101-worker/10-caller fixture checks; real recovery/MFA **146.0s PASS** with A/B
context/factor isolation, real trusted manual operator CLI, reused-email/new
subject rejection, replay/expired link and disabled IdP cases; real private-PG
backend J-02 **13.156s PASS** with rotation/compute/publication/revoke/replay.
Each real run recorded unchanged source scope and cleanup. See
[k03-completion-20261111.md](k03-completion-20261111.md) and
[k03-coverage-20261111.md](k03-coverage-20261111.md) for exact receipts/hashes.
Do not add overlapping test counts.

The trusted manual operator is the implemented coordinated revocation path;
an automatic event bridge is not a required missing feature. The local
0.078s/0.110s denials start after trusted commit, not an unreceived external IdP
event. The pinned cross-B recovery flow observed password_reset_applied=False;
it does not define a general rule for every recovery bearer link/browser.

Remaining gates are Quân's actual hosted mount/actions/fences/private-state
cleanup, the same-version hosted build, coordinated origin/CA/API/worker/schema/
test data, production policy/deployment and real two-tab/A→B/history/BFCache UX.
The broader green1079 runs had moving K-04 inputs and are not whole-WIP/runtime
qualification; the package95fb2d proof is auth-SQL/CLI/import component evidence
at its build checkpoint, not a package of later K-04 WIP. Preserve the earlier
combined second-start and J-01 failures as failed/unknown without inventing a
causal fix from the later local passes. No Quân message or shared acceptance
was sent/claimed; no already received inputs need redelivery.


## Read-only delta — 2026-11-11 08:38 +07

**HostedReady=false; inventory completed with exit 2.** No build, export, login,
browser, service, TLS probe or network request was started. Only the existing
canonical schema checker and local byte/source inventory were executed.

- The newer J-01 observed export is
  C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/j01-completion-20261110-e/producer-observed-current-20261111-266a849876b844ff9c5228f1281c1b72/j01-real.
  J-01's existing check_j01_schema.py returned exit 0 / VALID_FIXTURES=20;
  all code/status, date-time, extension, total/cursor/URL guards passed.
  Inventory found 28 JSON files / 20 payloads, no manifest.json.
  File-set SHA256: 4f01751aafd6075e3cefdfc6cdbf35cc95b7eebd46be745e2f848412d7cbeb04.
  This expands the earlier 24/17 export with job-output/schema evidence; it does
  not create a second contract. Session JSON/schema hashes remain
  5780fcbd877c0cb06f3bc1c602b28170e8dd88a5237b66e8872604cf774b2dae /
  c13fd0266bca779331345364523218b48e93091227b9c485c7ecc1f229b9c955.
- The old producer-current and final-current/j01 exports independently still
  validate 17 payloads each. Their recorded results below are historical
  checkpoints, not the latest producer directory.
- Current source mounting remains incomplete: main.tsx mounts
  StoreProvider→SessionGate→ServiceProvider→App. The actual hosted transport,
  port and HostedConnect exist, but createHostedClient, setHostedContext,
  bindHostedSession and HostedConnect have no application callers. The hosted
  guard/channel is not initialized by this tree. Renew/logout-all/signed-out
  actions are absent. Their presence as modules/types is not a mounted journey.
- No dashboard/web/dist/index.html exists in the inspected checkout. This
  inspection neither built a bundle nor searched all old TEMP build artifacts.
  No coordinated HTTPS origin/CA was supplied; browser CA trust and live TLS/API
  readiness were not revalidated. Old listener/certificate observations below
  remain historical and are not asserted as a new scan.
- Auth transport hashes: main.tsx
  ae8749a7656d2c8b4507c8f8ddde0d50a45aff0ed88dbba91fab4ecf329f8a73;
  service/index.tsx
  1d3a639451dc7b596fb6f595aaad0e7bf2787ae31f9b12dc6206cf4734892b10;
  hosted-client.ts
  37222d632a0e1315d781e3ed0e8e482587191152071b83b577dd3983c52d2a3b;
  hosted-port.ts
  57676ed4e7a1b572d56b9dee7e6dc7347fddf4aa311fe3ffd33a6da317e4b419;
  HostedConnect.tsx
  81765238e869b6dea190a9e2231e3aa8cb9a30343446444263054f70d7010db9.
  These are source inventory identities, not hosted build/acceptance hashes.

Quân still owns mounting/actions and browser private-state handling. K-03 owns
the independent recovery/operator follow-up; no extended live result is added
here. Existing manual revoke is not an automatic IdP bridge. Keep the accepted
callback four-reason redirect, logout_url, return_to='/' and boolean
can_create_workspace contract; do not reopen superseded aliases or request the
already received v5/v7/K-04/layout/workload inputs.

The executable block below now defaults to this newer existing export. Its
exit 2 intentionally leaves P02/P04/P05/P06/P07/P08 open. Canonical schema
validation and file hashing do not prove an authenticated browser journey.


Previous checkpoint: 2026-11-10, after the 23:19 +07:00 inspection. **HostedReady=false.**
Kiên's current request is backend work on Kiên's side; this document records
future consumer/browser prerequisites and a draft message only. No message is
sent to Quân, no shared acceptance is claimed, and this is not another contract.

## Previous checkpoint facts (2026-11-10)

Quân owns UI/consumer changes; J-01 owns backend, wire and canonical fixture
integration. Earlier UI WIP is preserved with its provenance; Kiên's chats do not
continue modifying it. The v5/v7/K-04 readers, layouts and workload were received;
do not request them again. Incoming ZIP SHA-256:
da575d85462af4e1d706e3f214e3ecb9d1ac694e346c205789fba98c57d37810.
Reader/K-04 patch SHA-256, matched to its README:
bd253564e51c6082d71001b01dfe22c88ef86294914159e808ef89c3d781e0c9.

| Boundary | Current implementation / remaining check |
| --- | --- |
| Callback | J-01 now emits a fixed 303 '/#/signed-out?reason=…' for domain failures, with login_cancelled, login_failed, account_disabled or idp_unavailable. Classification follows the trusted login/state boundary. Method/edge/admission failures retain their own behavior; not every rejected request is this redirect. |
| Logout | The selected field is logout_url; old redirect_to/idp_logout_url proposals are superseded. Disabled IdP logout returns local 204; enabled returns 200 with URL or null after local revoke. Null/204 is not proof IdP SSO ended. Session-bound ID-token-hint/retry details remain coordinated J-01/K-03 work; no extra public retry route is invented here. |
| Return route | Exact allowlisted return_to='/'. Restore a saved route only for the same user_id after current-authorized reads; account switch clears it and returns to '/'. A fresh login context never authorizes old pending responses. |
| Session | Canonical producer exports include boolean can_create_workspace, including needs-workspace allowed/denied examples. The field is a UI hint; the server still authorizes creation. Product/operator policy must not be inferred from email/onboarding/role. |
| Transport | Actual hosted-client.ts and hosted-port.ts now exist; service/index.tsx selects MODE='hosted' with no demo/bridge fallback. At inspection, createHostedClient/setHostedContext/bindHostedSession had no application callers, HostedConnect was not mounted, and signed-out/renew/logout-all UI actions were absent. This is incomplete mounting, not absence of the delivered transport modules. |
| Browser runner | Existing test_k03_browser_session.py builds the demo, uses HTTP static hosting and preview controls. It proves real demo tabs/BroadcastChannel/DOM behavior only. It cannot be relabelled as hosted authentication evidence. |
| TLS/runtime | No listener was observed on the legacy 18445/18446 ports. Old localhost certificate exists, but its thumbprint was not found in Windows user/machine roots; no reusable browser-trusted CA was demonstrated. The real recovery fixture removed its own TLS/container resources. A Python SSLContext is not Chrome trust. |
| Recovery/MFA | The previous real Keycloak→Mailpit→Authlib/JOSE→PG retained-OTP/force-login slice passed; see k03-recovery-mfa-verification-20261110.md. K-03 owns ongoing A/B expansion and trusted manual-operator evidence. Its explicit epoch SQL fixture is not an automatic IdP event bridge. Production IdP/assurance/lost-factor policy and future hosted evidence remain separate. |

Earlier canonical export:
C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/j01-completion-20261110-e/producer-current

The J-01 exporter intentionally writes 24 JSON files, including 17 payloads,
without manifest.json. Validate them with J-01's existing schema checker and
fingerprint their bytes; do not manufacture a second manifest/contract or edit
the old v7 export to add fields.

## Gates for a future hosted browser run

| Gate | Concrete input/evidence |
| --- | --- |
| P01 producer | Current canonical export, schema-checker result, file-set fingerprint, API source hashes and migration versions. These are WIP identities, not a release commit. |
| P02 consumer | Quân's mounted bootstrap/session/actions/workspace flow; current hosted build index and JS/CSS hashes; observed '/v1' requests from that exact bundle. Modules, unit readers and demo controls alone do not prove mounting. |
| P03 auth behavior | Current fixed callback reasons/logout_url/return_to/Session field, plus accepted renewal conflict/429/Retry-After recovery behavior. Do not replay callbacks or blindly retry mutations. No new alias or reset endpoint is added by this checklist. |
| P04 local TLS/deployment | Coordinated same-origin HTTPS assets and real API, exact issuer/client/redirect/logout URI, fresh verified CA and a demonstrated disposable-browser trust path; matching DB schema/roles, private object root and a separate live worker where jobs are exercised. No certificate bypass or public load. |
| P05 recovery authority | Link the passed real slice and ongoing A/B/manual-operator results to exact fixture/source/flow hashes. Preserve old OTP, force fresh login, verify signed assurance and revoke old app epochs through the trusted writer. Do not claim an automatic event bridge. |
| P06 data | Two synthetic actors/workspaces, owner/viewer and denied foreign source; safe fixture IDs and policy are explicit. No passwords/OTP seeds/codes/reset links/cookies in evidence. Existing material need not be redelivered merely for this preflight. |
| P07 browser | Actual two tabs, expired/revoked/renew/logout-all/switch, late responses/jobs/downloads and DOM/cache/draft cleanup. A26 requires observed navigation/BFCache restoration, not a dispatched pageshow event. |
| P08 results | Run ID, UTC time, actual source/build/asset/schema hashes and sanitized outcomes/timings. Kiên-only local backend/browser results are labelled as such; future Quân/Bảo joint acceptance remains unclaimed and is not substituted by a local pass. |

Keep progressing on Kiên-owned backend evidence independently. The concrete
hosted-run blockers are mounting/actions, a frozen hosted bundle, coordinated
TLS/API/worker/schema/test-data inputs and a real browser runner/trust path.
Current backend schema validation and prior MFA success do not close those gates.

## Draft Kiên → Quân — auth only, not sent

Quân ơi, mình đã nhận patch v5/v7/K-04, hai layout và workload; không cần gửi lại.
J-01 đang ghép chúng với backend hiện tại. Phần auth phía consumer cần nối/kiểm:

1. Bootstrap GET /v1/session và current workspace/capabilities, giữ màn được che
   đến khi xác nhận xong; bind session guard và hosted context thực sự vào app.
   Cookie đi cùng same-origin fetch; CSRF chỉ nằm trong memory, không storage/log.
2. Nối renew và logout-all theo route J-01 hiện có. Khi renewal conflict thì đọc
   lại session có thẩm quyền; hết/revoke thì dừng vòng poll/renew. Tôn trọng
   Retry-After có giới hạn; không replay callback hoặc tự lặp mutation.
3. Giữ return_to='/' chính xác. Chỉ khôi phục route của cùng user_id sau khi
   đọc lại được; switch account xóa route/draft cũ và về '/'. Không suy user từ
   token/header do client tự đặt.
4. Có signed-out UX cho đúng bốn reason đã chốt. Dùng logout_url; null/204 chỉ
   xác nhận logout Aletheia, không nói đã kết thúc IdP SSO và không tự switch
   rồi quay lại A. Retry/id_token_hint đi theo giao diện J-01 chốt, không tự ghép.
5. Context fence phải chặn mọi reply cũ cập nhật DOM/toast/history/draft/download.
   Wipe đúng account/workspace khi logout/revoke/switch; BroadcastChannel chỉ
   chứa tín hiệu/opaque context, không CSRF/cookie/token. Kiểm tab ẩn, quay lại,
   hai tab và cache/BFCache bằng browser thật trên đúng build.

Đây là bản nháp phối hợp, chưa gửi và chưa yêu cầu một lần chạy chung. Kiên
tiếp tục phần backend; kết quả local, producer/consumer và joint acceptance
được ghi riêng theo bằng chứng thực tế.

## Executable read-only inventory

Run this PowerShell block with existing dependencies. It invokes the canonical
J-01 checker, then hashes the current export and consumer files. No export,
build, install, browser, service start or login is performed. Optional TLS probes
require an explicit loopback HTTPS origin and CA together; leave both empty for
the current inventory. Endpoint bodies, cookies and headers are never printed.

~~~powershell
$K03Python = 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-k01-baseline-6d887dfb1fbb4aaba69613a8d61889fc\metric-review-venv\Scripts\python.exe'
$K03Repo = 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api'
$K03Producer = 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án 3\j01-completion-20261110-e\producer-observed-current-20261111-266a849876b844ff9c5228f1281c1b72\j01-real'
$K03PriorPythonPath = $env:PYTHONPATH
try {
  $env:PYTHONPATH = 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\j01-schema-validator-deps'
  $K03SchemaOutput = & $K03Python -B (Join-Path $K03Repo 'scripts/check_j01_schema.py') $K03Producer 2>&1
  if ($LASTEXITCODE -ne 0) { throw 'Canonical J-01 fixture check failed; inspect its safe fixture evidence separately.' }
  $K03SchemaOutput | Where-Object { "$_" -match '^(VALID_FIXTURES=[0-9]+|INVALID_CODE_STATUS_REJECTED=True|INVALID_DATE_TIME_REJECTED=True|EXTENSION_POLICY_MATCH=True|TOTAL_KIND_CONSISTENT=True|EMPTY_CURSOR_AND_ABSOLUTE_URL_REJECTED=True)$' }
} finally {
  if ($null -eq $K03PriorPythonPath) {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
  } else {
    $env:PYTHONPATH = $K03PriorPythonPath
  }
}
$K03Origin = ''
$K03CA = ''
$K03ProbeArgs = @()
if ($K03Origin -or $K03CA) {
  if (-not ($K03Origin -and $K03CA)) { throw 'Origin and CA must be supplied together.' }
  $K03ProbeArgs = @('--origin', $K03Origin, '--ca', $K03CA)
}
$K03Inventory = @'
import argparse, hashlib, json, re, ssl, sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

def digest(value):
    return hashlib.sha256(value).hexdigest()

def bounded_file(path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Invalid local artifact")
    return path.read_bytes()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.paths.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.paths.append(attrs["href"])

def probe(origin, ca):
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Explicit loopback HTTPS origin required")
    context = ssl.create_default_context(cafile=str(ca))
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
    origin = origin.rstrip("/")
    def get(path, expected):
        target = urljoin(origin + "/", path)
        parts = urlsplit(target)
        if (parts.scheme, parts.netloc) != (parsed.scheme, parsed.netloc) or parts.query or parts.fragment:
            raise ValueError("Same-origin query-free asset required")
        try:
            response = opener.open(Request(target, headers={"Accept": "*/*"}), timeout=5)
        except HTTPError as response_error:
            response = response_error
        with response:
            if response.status != expected:
                raise ValueError("Unexpected endpoint status")
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Endpoint response budget exceeded")
            return body, response.headers
    get("/health/ready", 200)
    print("TLS_VERIFIED_READINESS_STATUS=200")
    index, _ = get("/", 200)
    print("APP_INDEX_SHA256=" + digest(index))
    assets = Assets()
    assets.feed(index.decode("utf-8"))
    paths = sorted(set(assets.paths))
    if not paths or not any(urlsplit(path).path.endswith(".js") for path in paths):
        raise ValueError("Built hosted assets not identified")
    for number, path in enumerate(paths, 1):
        body, _ = get(path, 200)
        print("APP_ASSET_" + str(number) + "_SHA256=" + digest(body))
    _, headers = get("/v1/session", 401)
    if "no-store" not in headers.get("Cache-Control", "").lower():
        raise ValueError("Session no-store required")
    print("ANONYMOUS_SESSION_STATUS=401")
    print("TLS_ASSET_PROBE_SCOPE=readiness-and-bytes-only-no-authentication")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("producer", type=Path)
    parser.add_argument("--origin")
    parser.add_argument("--ca", type=Path)
    args = parser.parse_args()
    root = args.producer.resolve(strict=True)
    files = {}
    for path in sorted(root.glob("*.json")):
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Producer artifact outside its export")
        files[path.name] = digest(bounded_file(path))
    if not files:
        raise ValueError("Canonical producer export required")
    session = json.loads(bounded_file(root / "session.json"))
    if session.get("schema_version") != "product-api/v1-draft" or type(session.get("can_create_workspace")) is not bool:
        raise ValueError("Current canonical Session fixture required")
    print("CURRENT_PRODUCER_JSON_FILES=" + str(len(files)))
    print("CURRENT_PRODUCER_PAYLOAD_FILES=" + str(sum(not name.endswith(".schema.json") for name in files)))
    print("CURRENT_PRODUCER_MANIFEST_PRESENT=" + str((root / "manifest.json").is_file()).lower())
    print("CURRENT_PRODUCER_FILE_SET_SHA256=" + digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()))
    print("CURRENT_SESSION_JSON_SHA256=" + files["session.json"])
    print("CURRENT_SESSION_SCHEMA_SHA256=" + files["session.schema.json"])
    repo = args.repo.resolve(strict=True)
    for name in ("scripts/check_j01_schema.py", "scripts/export_j01_review.py",
                 "src/aletheia_lab/hosted_auth/callback_http.py",
                 "src/aletheia_lab/hosted_auth/logout_http.py",
                 "src/aletheia_lab/hosted_contract/session_capabilities.py",
                 "dashboard/web/src/main.tsx", "dashboard/web/src/App.tsx",
                 "dashboard/web/src/service/index.tsx", "dashboard/web/src/service/hosted-client.ts",
                 "dashboard/web/src/service/hosted-port.ts", "dashboard/web/src/service/hosted.ts",
                 "dashboard/web/src/components/hosted/HostedConnect.tsx"):
        path = (repo / name).resolve(strict=True)
        if not path.is_relative_to(repo):
            raise ValueError("Source input outside checkout")
        print("INPUT_" + re.sub("[^A-Za-z0-9]", "_", name).upper() + "_SHA256=" + digest(bounded_file(path)))
    if bool(args.origin) != bool(args.ca):
        raise ValueError("Origin and CA must be supplied together")
    if args.origin:
        probe(args.origin, args.ca)
    else:
        print("TLS_ASSET_PROBE=NOT_REQUESTED")
    print("RUNTIME_TRANSPORT_OBSERVATION=source-inventory-only-not-a-browser-run")
    print("HOSTED_READY=false")
    print("OPEN_GATES=P02_MOUNT_ACTIONS,P04_TLS_RUNTIME,P05_PRODUCTION_POLICY,P06_FIXTURE_BINDING,P07_BROWSER,P08_SCOPED_EVIDENCE")
    return 2

try:
    sys.exit(main())
except Exception as error:
    print("PREFLIGHT_INPUT_OR_PROBE_ERROR=" + type(error).__name__)
    print("HOSTED_READY=false")
    sys.exit(1)
'@
$K03Inventory | & $K03Python -B - $K03Repo $K03Producer @K03ProbeArgs
Write-Output "K03_PREFLIGHT_EXIT=$LASTEXITCODE"
~~~

Exit 2 means the read-only inventory completed while hosted gates remain open;
it is not a hosted pass. Exit 1 is an invalid input or failed probe. The optional
probe proves only TLS/readiness/asset bytes and anonymous-session denial, not
Chrome trust, authenticated behavior or acceptance.

## Earlier recorded inventory (24 JSON / 17 payloads)

The canonical checker was run read-only on the existing export:

- VALID_FIXTURES=17; invalid code/status/date-time, extension, total/cursor/URL guards passed.
- 24 JSON files; 17 payloads; manifest.json absent by exporter design.
- File-set SHA-256: c1c686ca54b3958deeb229721f9cf689ce4265bcc206751d0b0f96576c524cdd.
- Session JSON SHA-256: 5780fcbd877c0cb06f3bc1c602b28170e8dd88a5237b66e8872604cf774b2dae.
- Session schema SHA-256: c13fd0266bca779331345364523218b48e93091227b9c485c7ecc1f229b9c955.
- TLS probes remain unrequested; no browser/service/authentication was started.

The file-set fingerprint hashes the sorted filename-to-SHA map in memory. It is
evidence metadata only, not a new signed manifest, schema or wire contract.

## Historical v7 evidence — preserve, do not relabel

The earlier source-only inventory verified all 49 entries of
'Đồ Án/j01-producer-review-v7/manifest.json', version product-api/v1-draft.
Manifest SHA-256:
06290cf3958b87d2c6874dac8e28db323e74222ffaedba3f975001fbc1566dc3.

Its API/d4e7fb2 review index.tsx was
388c737203072a933ed6329f4bfcc3921606f9293f7ba00ac025c408a1f14a82;
the shared local live.ts was
cfd686241b99cff41bc7abf8e452ece6b32871b40238b2647ceeafc62b00a7d4.
The j01-quan-review index was
89eb4457bdba27a69abe49c22df8f7e377441a2bd27dc4144ae40828c2cb85cc;
kien-code index was
c03239b5c9c6f9ea75c6ee649a34616d37456fc952129e11d5c4f48b6a1ef444,
with hosted.ts/session.ts absent there. That run made no TLS probe and returned
HOSTED_READY=false / exit 2. It identifies that prior snapshot only.

Old b/c disagreements over aliases/callbacks/Session fields are superseded by e
and current J-01 code above. Keep old package provenance in J-01's evidence;
do not ask Quân to redeliver received files or make old ZIP uncertainty the
current producer export's contract.

Before any future hosted automation, coordinate with the owning runtime/frontend
chat. Use fresh disposable profiles, exact verified local origins and synthetic
data. Keep the user's browser profile untouched; never bypass a certificate
error or substitute demo controls for a hosted authentication result.
