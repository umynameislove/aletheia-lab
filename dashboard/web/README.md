# P6 dashboard (React)

The web UI for the P6 product: Start, Connect a project, Project overview, Investigation, Results &
reproduction, and Data & retention, plus the gated Counterfactual and Prospective quality areas.

It talks to the product only through `src/service/port.ts`, which holds the nine `ProductService`
methods under the backend's own names. Two ports implement them:

- `src/service/demo.ts` is the offline DemoProductPort, over the shared fixtures in `src/data/`.
- `src/service/live.ts` is the LiveProductPort, over the local bridge `python -m aletheia_lab.dashboard`.

```bash
npm ci
npm run dev           # demo, http://127.0.0.1:5274
npm run build:live    # Live build, served by the bridge with --static dashboard/web/dist
npm run dev:live      # Live while editing; /api goes to the bridge on 127.0.0.1:8765
npm test              # contract, product-view, port and live tests
```

Commands, the screen-to-method map and known gaps are in `docs/p6-dashboard-quickstart.md`.
