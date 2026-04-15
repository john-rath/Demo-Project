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
    "dashboards", "monitors", "notebooks", "slos", "services",
    "workflows", "incidents", "cases",
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
    """Load environment variables from .env file."""
    if not os.path.exists(env_path):
        print_warning(f".env file not found at {env_path}, using environment variables")
        return

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
    print_banner(f"Setup - {args.vertical}")

    try:
        # Load config to validate vertical exists
        config_loader = ConfigLoader("verticals")
        config = config_loader.load_vertical(args.vertical)
        print_success(f"Loaded config for vertical '{args.vertical}'")

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

        # Initialize API client
        client = get_dd_client(args.env)

        if args.dry_run:
            print_warning("DRY-RUN MODE: No resources will be created")

        # Use ResourceManager for orchestrated deployment
        mgr = ResourceManager(verticals_dir="verticals")
        print_header("Deploying resources...")

        result = mgr.deploy_selected(
            args.vertical, client, resources, dry_run=args.dry_run
        )

        # Print results per resource type
        for rtype, details in result.items():
            count = details.get("created", 0)
            errors = details.get("errors", 0)
            if count > 0 or errors > 0:
                status = f"{Colors.GREEN}{count} created{Colors.RESET}"
                if errors:
                    status += f", {Colors.RED}{errors} errors{Colors.RESET}"
                print(f"  {rtype:12s}  {status}")

        print_header("Setup Summary")
        total_created = sum(d.get("created", 0) for d in result.values())
        total_errors = sum(d.get("errors", 0) for d in result.values())

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
    print_banner(f"Teardown - {args.vertical}")

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

        # Use ResourceManager to check what's deployed
        mgr = ResourceManager(verticals_dir="verticals")
        print_header("Searching for demo resources...")
        status = mgr.get_status(args.vertical, client)

        total_found = sum(
            s.get("count", 0) for s in status.values()
            if isinstance(s, dict)
        )

        if total_found == 0:
            print_info("No demo resources found for this vertical")
            print()
            return

        # Show what we found
        for rtype, details in status.items():
            count = details.get("count", 0) if isinstance(details, dict) else 0
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
            args.vertical, client, resources, dry_run=args.dry_run
        )

        for rtype, details in result.items():
            deleted = details.get("deleted", 0)
            errors = details.get("errors", 0)
            if deleted > 0 or errors > 0:
                status_str = f"{Colors.GREEN}{deleted} removed{Colors.RESET}"
                if errors:
                    status_str += f", {Colors.RED}{errors} errors{Colors.RESET}"
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


def cmd_simulate(args: argparse.Namespace) -> None:
    """Handle 'simulate' command."""
    print_banner(f"Simulate - {args.vertical}")

    try:
        # Lazy import SimulatorEngine to avoid requiring OTel for other commands
        from dd_demo_toolkit.simulator.engine import SimulatorEngine

        # Load config
        config_loader = ConfigLoader("verticals")
        config = config_loader.load_vertical(args.vertical)
        print_success(f"Loaded config for vertical '{args.vertical}'")

        # Setup OTel (via environment)
        load_env_file(args.env)

        # Initialize simulator
        print_info(f"Initializing simulator with {args.interval}s tick interval...")
        engine = SimulatorEngine(config)

        # Show fleet info
        print_header("Simulator Fleet")
        print(f"  Devices: {len(engine.fleet)}")
        print(f"  Services: {len(engine.services)}")
        print()

        # Load incident plugin if available
        # (In real implementation, this would dynamically load plugins)

        print_info("Starting simulator... Press Ctrl+C to stop")
        print()

        # Run simulator
        engine.run(interval_sec=args.interval)

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


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="dd-demo",
        description="Datadog demo toolkit for sales engineers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  dd-demo list                                          List available verticals
  dd-demo setup --vertical healthcare                   Setup all resources for healthcare
  dd-demo setup --vertical healthcare --resources dashboards,monitors,workflows
                                                        Setup only specific resource types
  dd-demo simulate --vertical healthcare --interval 5   Run simulator with 5s ticks
  dd-demo teardown --vertical healthcare                Tear down resources for healthcare
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
        "--resources",
        help="Comma-separated resource types: dashboards,monitors,notebooks,slos,services,workflows,incidents,cases",
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # 'teardown' command
    teardown_parser = subparsers.add_parser("teardown", help="Remove demo resources")
    teardown_parser.add_argument(
        "--vertical",
        required=True,
        help="Vertical name to teardown",
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
        "--interval",
        type=float,
        default=1.0,
        help="Time between simulator ticks in seconds (default: 1.0)",
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
