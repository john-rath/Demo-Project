"""
dd-demo-toolkit visual layer.

A local single-user web UI that wraps the dd-demo-toolkit CLI.
See ./README.md for the architectural overview and the project plan
in repo-root for the phased roadmap.

The module is intentionally a wrapper, not a replacement: every action
the UI performs maps to a `dd-demo ...` subprocess invocation (Phase 2+)
or to a direct call into `dd_demo_toolkit.config.ConfigLoader` /
`dd_demo_toolkit.resources.ResourceManager`. The YAML files under
`verticals/` and the `.env` on disk remain the source of truth — the
UI just makes them easier to author and observe.
"""

__version__ = "0.1.0"
