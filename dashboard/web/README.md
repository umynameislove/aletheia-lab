# P6 dashboard (React)

The web UI for the P6 product: Start, Connect a project, Project overview, Investigation, Results &
reproduction, and Data & retention, plus the gated Counterfactual and Prospective quality areas.

It talks to the product only through `src/service/port.ts`, which holds the ten `ProductService`
methods under the backend's own names. Three ports implement the supported operations:

- `src/service/demo.ts` is the offline DemoProductPort, over the shared fixtures in `src/data/`.
- `src/service/live.ts` is the LiveProductPort, over the local bridge `python -m aletheia_lab.dashboard`.
- `src/service/hosted-port.ts` uses the hosted session, workspace and durable job APIs. Local-only operations stay explicitly unavailable.

Live questions compute metric observations through `analyze_observations`; they
do not infer a cause, call a model, execute imported code or apply repairs. Demo
answers retain their explicit mock runtime.

```bash
npm ci
npm run dev           # demo, http://127.0.0.1:5274
npm run build:live    # Live build, served by the bridge with --static dashboard/web/dist
npm run dev:live      # Live while editing; /api goes to the bridge on 127.0.0.1:8765
npm run build:hosted  # Hosted build; serve dist and /v1 through the same approved HTTPS origin
npm test              # contract, product-view, port and live tests
npm run e2e:hosted    # Build and exercise Chrome with a controlled local HTTP transport
```

Hosted starts with `GET /v1/session`, then server-listed workspaces. Sign-in goes to
`/v1/auth/start`; the UI never collects IdP passwords or invents an account. Set
`VITE_IDP_ORIGIN` at build time only to the configured HTTPS IdP origin if the server returns an
IdP logout URL. Cookies stay with same-origin fetch; CSRF and private forms stay in memory.
The visible hosted UI covers private content when its four-second authorization lease expires;
successful confirmation checks the session, selected workspace and current job. OS-throttled hidden
tabs, real IdP integration, staging capacity and joint acceptance require their own verification.
Saved result URLs retain opaque workspace/job/project/snapshot/result IDs across page navigation
and reload. A full IdP redirect uses the server-approved return route; returning to a particular open
hash requires verification with that configured IdP route. This UI does not invent a return target.

The shared mapping editor offers explicit class/unit recording locally and, in hosted mode, only
when `features["mapping.class_unit_v2"]` is true. Class labels come from the selected source field;
units are declared per metric, or explicitly unknown. A selected unit field must match the declaration.
Values are never converted. Each metric's direction and absolute threshold apply to all its classes.
The reader strictly dispatches `p6-product-view/v1` and `/v2`; legacy requests remain untagged v1.

Refresh, follow-up, export and project deletion are offered only when the workspace advertises
`projects.refresh`, `projects.follow_up`, `exports.download` or `projects.logical_delete` and the role
holds the matching actions; otherwise each control says why it is off. Refresh uploads a replacement ZIP
the person chooses, then sends its server source ID. Each CAS uses the PROJECT revision from the server
(never a membership revision); 412/428 ask for a review and the revision is read again. A follow-up's
job output is a summary: the child view is read from `GET results/{id}`. Export downloads the described
bytes only when scope, route, expiry, media type, size and SHA-256 match. Delete is a logical tombstone
with records retained; the receipt shows no counts. A request whose outcome is unknown keeps its key
and body for the retry.

When `features["sources.resumable"]` is true, the same upload form uses `/uploads`, hashes the
selected ZIP and its bounded 4 MiB parts, and keeps its upload ID only in the current attempt.
A manual retry reads server-confirmed progress and sends only missing parts; a completed upload
recovers its authoritative source ID. The existing `/sources` flow remains for false/missing flags.
When `features["sources.preview_lease"]` is true, preview/confirm use `/imports` and preserve the
server's workspace/source/preview ID/expiry. Expiry requires an explicit fresh preview and mapping
review; an unknown accepted request retains its original key. Neither flow automatically resends
an uncertain mutation. Hashing uses WebCrypto on a ZIP bounded by both the workspace limit and
the 200 MiB protocol ceiling; it is not an unbounded streaming-hash implementation.

Commands, the screen-to-method map and known gaps are in `docs/p6-dashboard-quickstart.md`.
