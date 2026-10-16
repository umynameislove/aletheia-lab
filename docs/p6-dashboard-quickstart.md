# P6 dashboard quickstart

The P6 dashboard is a React app in `dashboard/web`. It reaches the product only through the nine
`ProductService` methods. Two ports implement them:

- **DemoProductPort** (`dashboard/web/src/service/demo.ts`): offline and in memory, over the
  shared fixtures `synthetic_p6_view.json` (SHA-256 `0f541342…d957fd2`) and
  `p6_preview_import_v2.json` (`8e04f2e3…aad4c6`).
- **LiveProductPort** (`dashboard/web/src/service/live.ts`): posts each call to the local
  dashboard bridge, which forwards it to an in-process `ProductService(store_root)`.

The browser cannot import Python, so the bridge (`src/aletheia_lab/dashboard/bridge.py`) is the
UI-side adapter. It adds no product behaviour:

- method names, arguments, return values and `ProductError` codes pass through unchanged;
- it binds to `127.0.0.1` only;
- it accepts only same-origin requests that carry `X-P6-Bridge: 1`;
- it serves one call at a time and never logs payloads;
- an unexpected exception leaves as `service_failure`, never as a traceback;
- the store root is fixed at start-up and is never taken from a request.

The mode is chosen at build time and shown in the top bar (`LIVE`, and `DEMO` for demo data). A
Live failure is shown with its code. The app never falls back to demo data.

## Run

Setup, once:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cd dashboard/web
npm ci
```

Demo, offline (no Python needed):

```bash
cd dashboard/web
npm run dev
```

Then open <http://127.0.0.1:5274>.

Live, built app served by the bridge:

```bash
cd dashboard/web
npm run build:live
cd ../..
.venv/bin/python -m aletheia_lab.dashboard --store-root STORE --static dashboard/web/dist
```

Then open <http://127.0.0.1:8765>.

Live while editing the UI: run the bridge without `--static`, then run `npm run dev:live`. Vite
forwards `/api` to the bridge on port 8765; set `P6_BRIDGE` to use another bridge address.

`STORE` is any directory the service may own. The UI never sees it. The project folder is typed
on the Connect screen and travels only in the `preview_import` request body. It never appears in
the URL.

## Screens and methods

| Screen | Data and method | Demo | Live | Navigation |
| --- | --- | --- | --- | --- |
| Start | `demo_view` | fixture | service demo view | Connect, Investigation |
| Connect | `preview_import`, `confirm_import`, `refresh`, `analyze_mock` | fixture | yes | Investigation |
| Project overview | view fields; `refresh` | fixture | yes | Investigation, Data & retention |
| Investigation (table, 2D lineage, inspector) | view fields; `follow_up` | in memory | yes | `?result=&claim=&evidence=&node=&turn=` |
| Results & reproduction | view fields | fixture | yes | — |
| Data & retention | `export_report` (json, markdown, pdf), `delete_project` | json, markdown | all three | — |
| Counterfactual | `missing_evidence` marker only | not_available | not_available | gated |
| Prospective quality | none | out of scope | out of scope | gated |

## Deep links and reload

In Live, the URL carries the open `project`, `snapshot` and `result` IDs. A reload or back/forward
reopens exactly that result through `view`.

The same opaque IDs are kept in local storage, so a cold start can continue the open project from
Start: open its last result, refresh it, or ask about its snapshot. Only those three IDs are
stored. The store root, source path, mapping payload and imported content never are.

The contract has no project listing, so the UI offers no project picker. Confirming a preview
again is never used to find a project. A deleted or unknown result shows its code with a "Start
over" action; another result is never substituted. Deleting a project also clears its kept IDs.
The demo keeps its results in memory and puts no IDs in the URL.

## Tests

```bash
cd dashboard/web
npm test
npx tsc -b
```

```bash
.venv/bin/python -m pytest tests/unit/test_product_dashboard_bridge.py tests/integration/test_product_dashboard_bridge_http.py tests/integration/test_product_dashboard_live_flow.py
.venv/bin/ruff check src/aletheia_lab/dashboard tests
```

`test_product_dashboard_live_flow.py` drives the real service through the bridge with the
mapping shape the Connect screen sends. It covers analysis of the initial snapshot right after
confirm, a non-adverse refresh, and an adverse refresh with follow-up, view, the three exports
and delete, with sockets disabled. The web tests
include a parity check that `LiveProductPort` sends exactly the bridge's parameter names for all
nine methods.

## Known gaps

- The 1,000-node graph render benchmark has not been measured yet.
- PDF text is limited to the built-in font; Vietnamese glyphs need a font decision with the
  service owner.
