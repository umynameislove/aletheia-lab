"""Run the P6 dashboard against a local ProductService store.

python -m aletheia_lab.dashboard --store-root STORE [--static dashboard/web/dist] [--port 8765]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from aletheia_lab.dashboard.bridge import BridgeError, DashboardServer, load_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aletheia_lab.dashboard")
    parser.add_argument("--store-root", type=Path, required=True, help="ProductService store")
    parser.add_argument("--static", type=Path, default=None, help="built web app (dist)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.static is not None and not (args.static / "index.html").is_file():
        parser.error("--static must point at a built web app (run npm run build first)")
    service = load_service(args.store_root)
    if isinstance(service, BridgeError):
        # still start, so the dashboard shows the same safe code instead of a dead page
        print(f"product service unavailable: {service.code}")
    server = DashboardServer(service, port=args.port, static_root=args.static)
    print(f"P6 dashboard on http://127.0.0.1:{server.server_address[1]}/ (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
