"""Offline hosted configuration checks; this command does not start a server."""

from .runtime import main

if __name__ == "__main__":
    raise SystemExit(main())
