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

## Accessibility (Q12)

The audit used the Live build against a synthetic project. It covered every screen in the dark
and light themes, at 1440px, at 720px (the width of a 1440px screen at 200% zoom) and at 390px.

- **Reflow:** no screen scrolls sideways at 390px or 720px. Long service IDs are shortened in
  compact places; the full ID is in the tooltip, and copying the text copies the full ID.
- **Contrast:** all text meets WCAG AA (4.5:1, or 3:1 for large text) in both themes. The `·`
  separators are decorative and hidden from assistive tech.
- **Keyboard:**
  - Tab order runs skip link, then navigation, top bar, lineage nodes, claims and follow-up
    question.
  - Every stop shows a focus ring, or the accent border on inputs.
  - `j`/`k` move between claims; `Esc` clears the selection.
  - Dialogs trap Tab, close on `Esc` and return focus. Cancelling a delete calls nothing.
- **Colour:** status colours are always paired with words (adverse, abstain, unverified…).
- **Reduced motion:** transitions and animations are off under `prefers-reduced-motion`.
- **CSP:** the bridge serves the app with `script-src 'self'`; every script loads from a file.

`tests/a11y.test.mjs` keeps the guards that can be checked from source.

## Lineage render benchmark (Q08)

`dashboard/web/bench.html` mounts the real Lineage component with a synthetic 1,000-node graph:
1 snapshot, 599 evidence items, 399 claims and 1 disposition, joined by 1,795 edges. The node
order is shuffled on every run. Each run is timed from render until the browser has laid out
and painted (two animation frames). Every run must draw the same lanes in the same order.

```bash
cd dashboard/web
npm run bench:build
npm run bench:serve
```

Then open <http://127.0.0.1:5275/bench.html?n=1000&runs=30>. The page builds only into
`dist-bench/` and is never part of the app or the regular test suite.

Measured on a MacBook Pro, Apple M1 (MacBookPro17,1), 8 GB, macOS 15.3.2. Production React
build, 30 runs after 3 warm-up runs:

| Browser | Viewport | p50 | p95 | max | Same layout every run |
| --- | --- | --- | --- | --- | --- |
| Chromium 152 | 1440×900 | 29.6 ms | 42.5 ms | 46.0 ms | yes |
| Headless Chrome 155 | 1440×900 | 29.7 ms | 47.7 ms | 65.2 ms | yes |
| Headless Chrome 155 | 390×844 | 30.2 ms | 47.2 ms | 73.0 ms | yes |

The target was p95 ≤ 2 s. The layout itself (`src/data/lineage.ts`) sorts each lane by node ID,
so the drawing does not depend on arrival order. A dangling edge or a reused node ID stops the
drawing instead of producing a partial graph; the claim table stays complete either way.
`tests/lineage.test.mjs` checks permutation invariance, fail-closed handling, that no edge is
invented, and graph–table parity.

## Demo walkthrough

For the 7–10 minute recording, use only synthetic data:

1. Start the Live build.
2. Connect a synthetic project and show the import scope and the mapping checks.
3. Confirm the project and ask about the first snapshot.
4. Change a metric adversely, refresh, and ask again.
5. Trace a claim through the table, the lineage and the inspector.
6. Ask a follow-up and go back and forward between the results.
7. Export JSON, Markdown and PDF.
8. Delete the project and show that the old link is refused.

## Known gaps

- PDF text is limited to the built-in font; Vietnamese glyphs need a font decision with the
  service owner.
