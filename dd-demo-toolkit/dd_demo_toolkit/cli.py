"""
CLI for dd-demo-toolkit - Datadog demo management and simulation.

Provides commands for:
- setup: Deploy demo resources (dashboards, monitors, notebooks, SLOs, services, workflows, incidents, cases)
- teardown: Clean up deployed demo resources
- list: Show available verticals and their resources
- simulate: Run the demo simulator for a vertical
- status: Check deployed demo resources
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

from dd_demo_toolkit.config import ConfigLoader, ConfigError
from dd_demo_toolkit.utils.dd_api import DatadogAPIClient
from dd_demo_toolkit.resources.manager import ResourceManager

ALL_RESOURCE_TYPES = [
    "teams", "dashboards", "monitors", "notebooks", "slos", "services",
    "workflows", "incidents", "cases", "sds",
]


# ANSI color codes for professional terminal output
class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


def print_banner(title: str) -> None:
    """Print a professional banner with the given title."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  dd-demo-toolkit: {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}\n")


def print_success(message: str) -> None:
    """Print a success message in green."""
    print(f"{Colors.GREEN}✓{Colors.RESET} {message}")


def print_error(message: str) -> None:
    """Print an error message in red."""
    print(f"{Colors.RED}✗{Colors.RESET} {message}")


def print_info(message: str) -> None:
    """Print an info message in cyan."""
    print(f"{Colors.CYAN}ℹ{Colors.RESET} {message}")


def print_warning(message: str) -> None:
    """Print a warning message in yellow."""
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {message}")


def print_header(text: str) -> None:
    """Print a section header."""
    print(f"\n{Colors.BOLD}{Colors.WHITE}{text}{Colors.RESET}")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging based on verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def load_env_file(env_path: str) -> None:
    """Load environment variables from .env file if it exists."""
    if not os.path.exists(env_path):
        return  # No .env file — assume env vars are already set (e.g. via docker-compose)

    load_dotenv(env_path)
    print_info(f"Loaded environment from {env_path}")


def get_dd_client(env_path: str = ".env") -> DatadogAPIClient:
    """Initialize Datadog API client."""
    try:
        load_env_file(env_path)
        client = DatadogAPIClient()
        print_success(f"Connected to Datadog ({client.site})")
        return client
    except ValueError as e:
        print_error(f"Failed to initialize Datadog client: {e}")
        sys.exit(1)


def cmd_setup(args: argparse.Namespace) -> None:
    """Handle 'setup' command."""
    sub_vertical = getattr(args, "sub_vertical", None)
    label = args.vertical + (f" + {sub_vertical}" if sub_vertical else "")
    print_banner(f"Setup - {label}")

    try:
        # Load config to validate vertical exists. Overlay is loaded only
        # for validation here; the resource managers re-discover overlay
        # resource files separately by directory scan.
        config_loader = ConfigLoader("verticals")
        config = config_loader.load_vertical(args.vertical, sub_vertical=sub_vertical)
        print_success(f"Loaded config for vertical '{args.vertical}'")
        if sub_vertical:
            print_success(f"Loaded sub-vertical overlay '{sub_vertical}'")

        # Get resources to set up
        if args.resources:
            resources = [r.strip() for r in args.resources.split(",")]
            invalid = [r for r in resources if r not in ALL_RESOURCE_TYPES]
            if invalid:
                print_error(f"Unknown resource types: {', '.join(invalid)}")
                print_info(f"Valid types: {', '.join(ALL_RESOURCE_TYPES)}")
                sys.exit(1)
        else:
            resources = ALL_RESOURCE_TYPES

        print_info(f"Resources to setup: {', '.join(resources)}")

        # Local validation gate (offline, no credentials) — catch
        # deploy-blocking asset issues BEFORE any API call is made. Skippable
        # with --no-validate. See dd_demo_toolkit/validation/.
        if not getattr(args, "no_validate", False):
            from dd_demo_toolkit.validation import (
                ALL_RESOURCE_TYPES as VALIDATABLE,
                format_text as _format_text,
                summarize as _summarize,
                validate_vertical as _validate_vertical,
            )
            vtypes = [r for r in resources if r in VALIDATABLE]
            if vtypes:
                findings = _validate_vertical(
                    args.vertical, sub_vertical=sub_vertical,
                    verticals_dir="verticals", resource_types=vtypes,
                )
                vsummary = _summarize(findings)
                if vsummary["errors"] > 0:
                    print_header("Validation")
                    print(_format_text(findings, use_color=sys.stdout.isatty()))
                    print_error(
                        f"{vsummary['errors']} blocking issue(s) found before deploy. "
                        "Fix them, or re-run with --no-validate to override."
                    )
                    sys.exit(1)
                if vsummary["warnings"] > 0:
                    print_warning(
                        f"Validation: {vsummary['warnings']} warning(s) "
                        "(non-blocking) — run 'dd-demo validate' for detail."
                    )
                else:
                    print_success("Local validation passed")

        # Initialize API client
        client = get_dd_client(args.env)

        if args.dry_run:
            print_warning("DRY-RUN MODE: No resources will be created")

        # Use ResourceManager for orchestrated deployment
        mgr = ResourceManager(verticals_dir="verticals")

        # Clean up existing resources first if --clean flag is set
        if getattr(args, "clean", False):
            print_header("Cleaning up existing resources...")
            teardown_result = mgr.teardown_selected(
                args.vertical, client, resources, dry_run=args.dry_run
            )
            total_deleted = teardown_result.get("summary", {}).get("total_deleted", 0)
            teardown_errors = teardown_result.get("summary", {}).get("total_errors", 0)
            if total_deleted > 0:
                print_success(f"Cleaned up {total_deleted} existing resource(s)")
            if teardown_errors > 0:
                print_warning(f"{teardown_errors} error(s) during cleanup (continuing with deploy)")
            if total_deleted == 0 and teardown_errors == 0:
                print_info("No existing resources found — clean slate")

        print_header("Deploying resources...")

        result = mgr.deploy_selected(
            args.vertical, client, resources, dry_run=args.dry_run
        )

        # If a sub-vertical overlay was specified, layer its resources on
        # top of the base vertical's deployment. Overlay resources reuse
        # the base vertical's tag standards (vertical:<base>,
        # dd-demo-toolkit:true) — see ResourceManager.deploy_overlay_selected.
        if sub_vertical:
            print_header(f"Deploying overlay '{sub_vertical}' resources...")
            overlay_result = mgr.deploy_overlay_selected(
                args.vertical, sub_vertical, client, resources,
                dry_run=args.dry_run,
            )
            for rtype, details in overlay_result.items():
                if rtype == "summary" or not isinstance(details, dict):
                    continue
                count = details.get("total_created", 0)
                errors = details.get("total_errors", 0)
                if count > 0 or errors > 0:
                    status = f"{Colors.GREEN}{count} created{Colors.RESET}"
                    if errors:
                        status += f", {Colors.RED}{errors} errors{Colors.RESET}"
                    print(f"  overlay/{rtype:10s}  {status}")
            ov_summary = overlay_result.get("summary", {})
            ov_total = ov_summary.get("total_created", 0)
            ov_errors = ov_summary.get("total_errors", 0)
            # Roll overlay totals into the top-level summary so the
            # "Setup Summary" panel is accurate.
            summary = result.setdefault("summary", {})
            summary["total_created"] = (
                summary.get("total_created", 0) + ov_total
            )
            summary["total_errors"] = (
                summary.get("total_errors", 0) + ov_errors
            )

        # Print results per resource type
        summary = result.get("summary", {})
        for rtype, details in result.items():
            if rtype == "summary" or not isinstance(details, dict):
                continue
            count = details.get("total_created", 0)
            errors = details.get("total_errors", 0)
            if count > 0 or errors > 0:
                status = f"{Colors.GREEN}{count} created{Colors.RESET}"
                if errors:
                    status += f", {Colors.RED}{errors} errors{Colors.RESET}"
                print(f"  {rtype:12s}  {status}")

        print_header("Setup Summary")
        total_created = summary.get("total_created", 0)
        total_errors = summary.get("total_errors", 0)

        if args.dry_run:
            print_info("(DRY-RUN) No resources were actually created")
        elif total_errors == 0:
            print_success(f"All {total_created} resources deployed successfully!")
        else:
            print_warning(f"{total_created} created, {total_errors} errors")

        print_info(f"All resources tagged: vertical:{args.vertical} dd-demo-toolkit:true")
        print()

    except ConfigError as e:
        print_error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Setup failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def cmd_teardown(args: argparse.Namespace) -> None:
    """Handle 'teardown' command."""
    # Resolve scope: one vertical vs. a full all-verticals sweep.
    all_verticals = getattr(args, "all_verticals", False)
    if all_verticals and args.vertical:
        print_error("Pass either --vertical or --all-verticals, not both.")
        sys.exit(2)
    if not all_verticals and not args.vertical:
        print_error("Either --vertical <name> or --all-verticals is required.")
        sys.exit(2)

    scope_label = "ALL verticals" if all_verticals else args.vertical
    vertical_arg: Optional[str] = None if all_verticals else args.vertical
    print_banner(f"Teardown - {scope_label}")

    try:
        # Get resources to tear down
        if args.resources:
            resources = [r.strip() for r in args.resources.split(",")]
        else:
            resources = ALL_RESOURCE_TYPES

        print_info(f"Resources to teardown: {', '.join(resources)}")

        # Initialize API client
        client = get_dd_client(args.env)

        if args.dry_run:
            print_warning("DRY-RUN MODE: No resources will be deleted")

        mgr = ResourceManager(verticals_dir="verticals")

        if all_verticals:
            # All-verticals sweep: skip the per-vertical preview (get_status
            # is scoped to a single vertical). Prompt loudly, then run.
            print_header("All-verticals sweep")
            print_warning(
                "This will delete every resource tagged 'dd-demo-toolkit:true' "
                "across every vertical, including orphans from renamed or "
                "removed verticals."
            )
            if not args.force and not args.dry_run:
                response = input(
                    f"{Colors.YELLOW}Type 'yes' to confirm full cleanup: {Colors.RESET}"
                )
                if response.lower() != "yes":
                    print_info("Teardown cancelled")
                    return
        else:
            # Use ResourceManager to check what's deployed
            print_header("Searching for demo resources...")
            status = mgr.get_status(vertical_arg, client)

            total_found = sum(
                s.get("total", 0) for s in status.values()
                if isinstance(s, dict)
            )

            if total_found == 0:
                print_info("No demo resources found for this vertical")
                print()
                return

            # Show what we found
            for rtype, details in status.items():
                count = details.get("total", 0) if isinstance(details, dict) else 0
                if count > 0:
                    print(f"  {rtype:12s}  {Colors.CYAN}{count} found{Colors.RESET}")

            # Confirm deletion (unless --force)
            if not args.force and not args.dry_run:
                print(f"\n{Colors.YELLOW}Warning:{Colors.RESET} This will delete {total_found} resources")
                response = input("Are you sure? (type 'yes' to confirm): ")
                if response.lower() != "yes":
                    print_info("Teardown cancelled")
                    return

        # Perform teardown
        print_header("Tearing down resources...")
        result = mgr.teardown_selected(
            vertical_arg, client, resources, dry_run=args.dry_run
        )

        # Per-resource-type summary. Each manager uses slightly different
        # count keys (total_deleted / total_resolved / total_closed /
        # total_deregistered), so read any int-valued "total_*" key except
        # total_errors. The "summary" aggregate key is skipped.
        _SKIP_RTYPES = {"summary"}
        for rtype, details in result.items():
            if rtype in _SKIP_RTYPES or not isinstance(details, dict):
                continue
            deleted = sum(
                v for k, v in details.items()
                if k.startswith("total_")
                and k != "total_errors"
                and isinstance(v, int)
            )
            error_count = details.get("total_errors", 0) or 0
            if deleted > 0 or error_count > 0:
                status_str = f"{Colors.GREEN}{deleted} removed{Colors.RESET}"
                if error_count:
                    status_str += f", {Colors.RED}{error_count} errors{Colors.RESET}"
                print(f"  {rtype:12s}  {status_str}")

        if args.dry_run:
            print_info("(DRY-RUN) No resources were actually deleted")
        else:
            print_success("Teardown complete!")
        print()

    except Exception as e:
        print_error(f"Teardown failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    """Handle 'list' command."""
    print_banner("Available Verticals")

    try:
        config_loader = ConfigLoader("verticals")
        verticals = config_loader.list_verticals()

        if not verticals:
            print_warning("No verticals found")
            return

        if args.vertical:
            # Show details for specific vertical
            config = config_loader.load_vertical(args.vertical)
            vertical_info = config.get("vertical", {})

            print_header(vertical_info.get("display_name", args.vertical))
            print(f"  Name: {vertical_info.get('name')}")
            print(f"  Description: {vertical_info.get('description', 'N/A')}")

            # Show resources
            print_header("Resources Included")

            resources = {
                "Services": config.get("services", []),
                "Device Categories": config.get("device_categories", {}).keys(),
            }

            for resource_type, items in resources.items():
                if items:
                    print(f"\n{Colors.BOLD}{resource_type}:{Colors.RESET}")
                    if isinstance(items, list):
                        for item in items:
                            name = item.get("name") if isinstance(item, dict) else item
                            print(f"  • {name}")
                    else:
                        for item in items:
                            print(f"  • {item}")

            # Surface available sub-vertical overlays so a user running
            # 'dd-demo list --vertical healthcare' can discover them.
            overlays = config_loader.list_overlays(args.vertical)
            if overlays:
                print(f"\n{Colors.BOLD}Sub-vertical overlays:{Colors.RESET}")
                for ov in overlays:
                    print(f"  • {ov}  (use --sub-vertical {ov})")

        else:
            # List all verticals
            print(f"Found {len(verticals)} available vertical(s):\n")

            for vertical_name in verticals:
                try:
                    config = config_loader.load_vertical(vertical_name)
                    vertical_info = config.get("vertical", {})
                    display_name = vertical_info.get("display_name", vertical_name)
                    description = vertical_info.get("description", "")

                    print(f"{Colors.BOLD}{display_name}{Colors.RESET}")
                    if description:
                        print(f"  {description}")

                    # Show quick stats
                    services = config.get("services", [])
                    devices = sum(
                        len(cat.get("devices", []))
                        for cat in config.get("device_categories", {}).values()
                    )
                    print(f"  {services.__len__()} services, {devices} device types")
                    print()

                except ConfigError as e:
                    print_warning(f"Could not load {vertical_name}: {e}")

        print()

    except ConfigError as e:
        print_error(f"Failed to load verticals: {e}")
        sys.exit(1)


def _load_plugins(engine, vertical_name: str) -> None:
    """
    Dynamically discover and load incident plugins for a vertical.

    Scans verticals/{vertical}/plugins/ for Python files, imports any class
    that subclasses IncidentPlugin, and registers it with the engine.
    """
    import importlib.util
    from dd_demo_toolkit.simulator.plugins import IncidentPlugin

    plugins_dir = Path("verticals") / vertical_name / "plugins"
    if not plugins_dir.is_dir():
        return

    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"verticals.{vertical_name}.plugins.{py_file.stem}", py_file
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, IncidentPlugin)
                    and attr is not IncidentPlugin
                ):
                    plugin = attr()
                    engine.register_plugin(plugin)
                    print_success(f"Loaded plugin: {plugin.get_incident_name()}")
        except Exception as exc:
            print_warning(f"Failed to load plugin {py_file.name}: {exc}")


def _load_overlay_plugins(
    engine, vertical_name: str, sub_vertical: str
) -> None:
    """
    Discover and load incident plugins from a sub-vertical overlay.

    Scans ``verticals/<vertical>/overlays/<sub_vertical>/plugins/*.py`` for
    Python modules that contain ``IncidentPlugin`` subclasses and registers
    them with the engine. Mirrors ``_load_plugins`` but rooted at the
    overlay directory.
    """
    import importlib.util
    from dd_demo_toolkit.simulator.plugins import IncidentPlugin

    plugins_dir = (
        Path("verticals") / vertical_name / "overlays" / sub_vertical / "plugins"
    )
    if not plugins_dir.is_dir():
        return

    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"verticals.{vertical_name}.overlays.{sub_vertical}.plugins.{py_file.stem}",
                py_file,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, IncidentPlugin)
                    and attr is not IncidentPlugin
                ):
                    plugin = attr()
                    engine.register_plugin(plugin)
                    print_success(
                        f"Loaded overlay plugin: {plugin.get_incident_name()}"
                    )
        except Exception as exc:
            print_warning(
                f"Failed to load overlay plugin {py_file.name}: {exc}"
            )


def cmd_simulate(args: argparse.Namespace) -> None:
    """Handle 'simulate' command."""
    print_banner(f"Simulate - {args.vertical}")

    try:
        # Lazy import SimulatorEngine to avoid requiring OTel for other commands
        from dd_demo_toolkit.simulator.engine import SimulatorEngine

        # Load config (with optional sub-vertical overlay merged in)
        sub_vertical = getattr(args, "sub_vertical", None)
        config_loader = ConfigLoader("verticals")
        config = config_loader.load_vertical(
            args.vertical, sub_vertical=sub_vertical
        )
        print_success(f"Loaded config for vertical '{args.vertical}'")
        if sub_vertical:
            print_success(f"Merged sub-vertical overlay '{sub_vertical}'")

        # Setup OTel (via environment)
        load_env_file(args.env)

        # Get interval: prefer CLI arg, fall back to env var, then default
        interval = args.interval
        env_interval = os.getenv("EMIT_INTERVAL")
        if env_interval and interval == 1.0:  # 1.0 is the argparse default
            interval = float(env_interval)

        # Initialize simulator
        print_info(f"Initializing simulator with {interval}s tick interval...")
        engine = SimulatorEngine(config)

        # Show fleet info
        print_header("Simulator Fleet")
        print(f"  Devices: {len(engine.fleet)}")
        print(f"  Services: {len(engine.services)}")
        print()

        # Load incident plugins from vertical's plugins directory.  When a
        # sub-vertical overlay is active, also load the overlay's plugins
        # so its scripted incidents (e.g. the BD Pyxis cascade) run on top
        # of the base vertical's existing plugins.
        _load_plugins(engine, args.vertical)
        if sub_vertical:
            _load_overlay_plugins(engine, args.vertical, sub_vertical)

        print_info("Starting simulator... Press Ctrl+C to stop")
        print()

        # Run simulator
        engine.run(interval_sec=interval)

    except ConfigError as e:
        print_error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        print_info("Simulator stopped by user")
    except Exception as e:
        print_error(f"Simulation failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    """Handle 'status' command."""
    print_banner(f"Status - {args.vertical}")

    try:
        # Load config to validate vertical
        config_loader = ConfigLoader("verticals")
        config = config_loader.load_vertical(args.vertical)
        vertical_info = config.get("vertical", {})
        print_success(f"Vertical: {vertical_info.get('display_name', args.vertical)}")

        # Initialize API client
        client = get_dd_client(args.env)

        # Use ResourceManager for comprehensive status
        mgr = ResourceManager(verticals_dir="verticals")
        print_header("Deployed Demo Resources")

        try:
            status = mgr.get_status(args.vertical, client)
            total = 0

            for rtype in ALL_RESOURCE_TYPES:
                details = status.get(rtype, {})
                count = details.get("count", 0) if isinstance(details, dict) else 0
                total += count
                indicator = Colors.GREEN if count > 0 else Colors.DIM
                print(f"  {rtype:12s}  {indicator}{count}{Colors.RESET}")

            if total == 0:
                print(f"\n{Colors.YELLOW}No demo resources deployed for this vertical{Colors.RESET}")
                print_info("Run: dd-demo setup --vertical " + args.vertical)
            else:
                print(f"\n{Colors.GREEN}{total} total resources deployed{Colors.RESET}")

        except Exception as e:
            print_warning(f"Could not fetch resource details: {e}")

        print()

    except ConfigError as e:
        print_error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Status check failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def cmd_validate(args: argparse.Namespace) -> None:
    """Handle 'validate' command — local, credential-free asset linting.

    Runs the STYLE_GUIDE rules as machine checks over a vertical's (and an
    optional overlay's) monitors/dashboards/notebooks/workflows/SLOs. No
    Datadog credentials or network required. Exits 1 if any ERROR finding is
    present (or any finding at all under --strict) so it can gate CI and the
    'setup' command.
    """
    from dd_demo_toolkit.validation import (
        ALL_RESOURCE_TYPES as VALIDATABLE,
        format_text,
        summarize,
        validate_vertical,
    )

    sub_vertical = getattr(args, "sub_vertical", None)
    resource_types: Optional[List[str]] = None
    if args.resources:
        resource_types = [r.strip() for r in args.resources.split(",")]
        invalid = [r for r in resource_types if r not in VALIDATABLE]
        if invalid:
            print_error(f"Cannot validate resource types: {', '.join(invalid)}")
            print_info(f"Validatable types: {', '.join(VALIDATABLE)}")
            sys.exit(2)

    findings = validate_vertical(
        args.vertical,
        sub_vertical=sub_vertical,
        verticals_dir="verticals",
        resource_types=resource_types,
    )
    s = summarize(findings)

    if getattr(args, "format", "text") == "json":
        import json as _json
        print(_json.dumps(
            {"summary": s, "findings": [f.as_dict() for f in findings]},
            indent=2,
        ))
    else:
        label = args.vertical + (f" + {sub_vertical}" if sub_vertical else "")
        print_banner(f"Validate - {label}")
        print(format_text(findings, use_color=sys.stdout.isatty()))
        print()

    if s["errors"] > 0 or (getattr(args, "strict", False) and s["warnings"] > 0):
        sys.exit(1)


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="dd-demo",
        description="Datadog demo toolkit for sales engineers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  dd-demo list                                          List available verticals
  dd-demo validate --vertical healthcare                Lint a vertical's assets locally (no credentials)
  dd-demo setup --vertical healthcare                   Setup all resources for healthcare
  dd-demo setup --vertical healthcare --resources dashboards,monitors,workflows
                                                        Setup only specific resource types
  dd-demo simulate --vertical healthcare --interval 5   Run simulator with 5s ticks
  dd-demo teardown --vertical healthcare                Tear down resources for healthcare
  dd-demo teardown --all-verticals                      Sweep every toolkit-managed resource (incl. orphans)
  dd-demo status --vertical healthcare                  Check deployed resources
        """,
    )

    # Global options
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # 'setup' command
    setup_parser = subparsers.add_parser("setup", help="Setup demo resources")
    setup_parser.add_argument(
        "--vertical",
        required=True,
        help="Vertical name to setup",
    )
    setup_parser.add_argument(
        "--sub-vertical",
        dest="sub_vertical",
        default=None,
        help=(
            "Optional sub-vertical overlay name. Loads "
            "verticals/<vertical>/overlays/<name>.yaml (additive simulator "
            "config) and verticals/<vertical>/overlays/<name>/ (additional "
            "monitors, dashboards, notebooks, plugins, etc.). Overlay "
            "resources are tagged with the BASE vertical's name to cohere "
            "with existing tag standards."
        ),
    )
    setup_parser.add_argument(
        "--resources",
        help="Comma-separated resource types: dashboards,monitors,notebooks,slos,services,workflows,incidents,cases",
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )
    setup_parser.add_argument(
        "--clean",
        action="store_true",
        help="Tear down existing resources before deploying (idempotent rebuild)",
    )
    setup_parser.add_argument(
        "--no-validate",
        dest="no_validate",
        action="store_true",
        help="Skip the local asset-validation gate (deploy even if it reports errors).",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # 'teardown' command
    teardown_parser = subparsers.add_parser("teardown", help="Remove demo resources")
    teardown_parser.add_argument(
        "--vertical",
        help="Vertical name to teardown. Required unless --all-verticals is set.",
    )
    teardown_parser.add_argument(
        "--all-verticals",
        action="store_true",
        help=(
            "Sweep every toolkit-managed resource across every vertical, "
            "including orphans from renamed or removed verticals. "
            "Matches the 'dd-demo-toolkit:true' marker."
        ),
    )
    teardown_parser.add_argument(
        "--resources",
        help="Comma-separated resource types to remove",
    )
    teardown_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    teardown_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )
    teardown_parser.set_defaults(func=cmd_teardown)

    # 'list' command
    list_parser = subparsers.add_parser("list", help="List verticals and resources")
    list_parser.add_argument(
        "--vertical",
        help="Show details for specific vertical",
    )
    list_parser.set_defaults(func=cmd_list)

    # 'simulate' command
    simulate_parser = subparsers.add_parser("simulate", help="Run demo simulator")
    simulate_parser.add_argument(
        "--vertical",
        required=True,
        help="Vertical name to simulate",
    )
    simulate_parser.add_argument(
        "--sub-vertical",
        dest="sub_vertical",
        default=None,
        help=(
            "Optional sub-vertical overlay. Merges overlay devices/services "
            "into the base config and loads overlay plugins (e.g. the BD "
            "Pyxis cascade). Same name as on 'dd-demo setup'."
        ),
    )
    simulate_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Time between simulator ticks in seconds (default: 1.0, or set EMIT_INTERVAL env var)",
    )
    simulate_parser.set_defaults(func=cmd_simulate)

    # 'status' command
    status_parser = subparsers.add_parser("status", help="Check demo resources status")
    status_parser.add_argument(
        "--vertical",
        required=True,
        help="Vertical name to check",
    )
    status_parser.set_defaults(func=cmd_status)

    # 'validate' command — local asset linting, no credentials needed
    validate_parser = subparsers.add_parser(
        "validate",
        help="Lint a vertical's assets locally against the Style Guide (no credentials)",
    )
    validate_parser.add_argument(
        "--vertical", required=True, help="Vertical name to validate",
    )
    validate_parser.add_argument(
        "--sub-vertical", dest="sub_vertical", default=None,
        help="Optional overlay to validate alongside the base vertical.",
    )
    validate_parser.add_argument(
        "--resources",
        help="Comma-separated subset: monitors,dashboards,notebooks,workflows,slos",
    )
    validate_parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as failures (exit 1 on any finding).",
    )
    validate_parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text). 'json' is for the UI / CI.",
    )
    validate_parser.set_defaults(func=cmd_validate)

    # Parse arguments
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Show help if no command
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Execute command
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
        print_info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
