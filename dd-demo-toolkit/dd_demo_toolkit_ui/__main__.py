"""Allow `python -m dd_demo_toolkit_ui` as an alternative to `dd-demo-ui`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
