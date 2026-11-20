"""Read-only hosted operator observation entrypoint; credentials stay in environment."""

from aletheia_lab.hosted.operator_observation import main

if __name__ == "__main__":
    raise SystemExit(main())
