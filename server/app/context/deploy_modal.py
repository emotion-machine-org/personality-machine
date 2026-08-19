#!/usr/bin/env python3
"""Deploy Modal apps for the Context Engine.

This script deploys the Modal-based services used by the context engine:
- modal_behavior_executor.py - Executes behaviors via Modal Functions

Environment Selection:
    The Modal environment is determined by (in order of precedence):
    1. --env CLI argument
    2. MODAL_ENVIRONMENT env var
    3. Default: "main" (production)

Usage:
    # Deploy all context Modal apps to staging
    python -m app.context.deploy_modal --env staging

    # Deploy using MODAL_ENVIRONMENT env var
    MODAL_ENVIRONMENT=staging python -m app.context.deploy_modal

    # Deploy to production (main)
    python -m app.context.deploy_modal --env main

    # Dry run (print commands without executing)
    python -m app.context.deploy_modal --env staging --dry-run

    # Deploy a specific app only
    python -m app.context.deploy_modal --env staging --app behavior-executor

    # Programmatic deployment (for FastAPI startup)
    from app.context.deploy_modal import deploy_all
    await deploy_all()  # Uses MODAL_ENVIRONMENT env var
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Default Modal environment
DEFAULT_MODAL_ENV = "main"

# Modal apps to deploy
MODAL_APPS = {
    "behavior-executor": "modal_behavior_executor.py",
}


def get_modal_environment(cli_env: str | None = None) -> str:
    """Get the Modal environment from CLI arg or env var."""
    if cli_env:
        return cli_env
    return os.getenv("MODAL_ENVIRONMENT", DEFAULT_MODAL_ENV)


def run_cli(cmd: list[str], dry_run: bool = False) -> int:
    """Run a CLI command, optionally as dry run."""
    logger.info("$ %s", " ".join(cmd))
    if dry_run:
        return 0
    return subprocess.call(cmd)


def deploy_app_cli(
    app_path: Path,
    env: str,
    dry_run: bool = False,
) -> int:
    """Deploy a single Modal app using CLI."""
    if not app_path.exists():
        logger.error("File not found: %s", app_path)
        return 1

    cmd = ["modal", "deploy", "--env", env, str(app_path)]
    return run_cli(cmd, dry_run=dry_run)


async def deploy_app_programmatic(app_module: str, env: str) -> bool:
    """Deploy a Modal app programmatically.

    Args:
        app_module: The module path (e.g., "app.context.modal_behavior_executor")
        env: Modal environment name

    Returns:
        True if successful, False otherwise
    """
    try:
        # Import the module and get the app
        import importlib

        import modal

        module = importlib.import_module(app_module)
        app = getattr(module, "app", None)

        if app is None:
            logger.error("No 'app' found in module %s", app_module)
            return False

        logger.info("Deploying %s to environment '%s'...", app_module, env)

        # Deploy with environment
        with modal.enable_output():
            app.deploy(environment_name=env)

        logger.info("Successfully deployed %s", app_module)
        return True

    except Exception as e:
        logger.error("Failed to deploy %s: %s", app_module, e)
        return False


async def deploy_all(env: str | None = None) -> bool:
    """Deploy all Modal apps programmatically.

    Args:
        env: Modal environment name. If None, uses MODAL_ENVIRONMENT env var.

    Returns:
        True if all deployments successful, False otherwise
    """
    modal_env = env or get_modal_environment()
    logger.info("Deploying all Modal apps to environment '%s'", modal_env)

    # Map filenames to module paths
    module_map = {
        "modal_behavior_executor.py": "app.context.modal_behavior_executor",
    }

    success = True
    for app_name, filename in MODAL_APPS.items():
        module_path = module_map.get(filename)
        if module_path:
            if not await deploy_app_programmatic(module_path, modal_env):
                success = False
        else:
            logger.warning("No module mapping for %s", filename)

    return success


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy Modal apps for the Context Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--env",
        dest="env",
        default=None,
        help="Modal environment name (default: MODAL_ENVIRONMENT or 'main')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--app",
        dest="app",
        choices=list(MODAL_APPS.keys()),
        default=None,
        help="Deploy a specific app only (default: deploy all)",
    )
    parser.add_argument(
        "--programmatic",
        action="store_true",
        help="Use programmatic deployment instead of CLI",
    )
    args = parser.parse_args()

    # Determine environment
    modal_env = get_modal_environment(args.env)
    logger.info("Using Modal environment: %s", modal_env)

    # Programmatic deployment
    if args.programmatic:
        return 0 if asyncio.run(deploy_all(modal_env)) else 1

    # Get the context directory (where this script lives)
    context_dir = Path(__file__).resolve().parent

    # Determine which apps to deploy
    if args.app:
        apps_to_deploy = {args.app: MODAL_APPS[args.app]}
    else:
        apps_to_deploy = MODAL_APPS

    # Deploy apps using CLI
    code = 0
    for app_name, filename in apps_to_deploy.items():
        logger.info("Deploying %s...", app_name)
        app_path = context_dir / filename
        rc = deploy_app_cli(app_path, env=modal_env, dry_run=args.dry_run)
        if rc != 0:
            logger.error("Failed to deploy %s (exit code %d)", app_name, rc)
            code = code or rc
        else:
            logger.info("Successfully deployed %s", app_name)

    return code


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    sys.exit(main())
