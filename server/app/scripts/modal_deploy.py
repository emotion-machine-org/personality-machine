#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def run(cmd: list[str]) -> int:
    logger.info("$ %s", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Modal apps for EM backend")
    parser.add_argument(
        "--env", dest="env", default=None, help="Modal environment name (e.g., main, staging)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    args = parser.parse_args()

    # Use --env flag if provided, otherwise fall back to MODAL_ENVIRONMENT env var
    modal_env = args.env or os.getenv("MODAL_ENVIRONMENT")
    if modal_env:
        logger.info("Using Modal environment: %s", modal_env)
    else:
        logger.warning("No Modal environment specified (use --env or set MODAL_ENVIRONMENT)")

    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent
    workers_dir = app_dir / "modals" / "workers"
    services_dir = app_dir / "modals" / "services"

    code = 0

    # Deploy services first (workers may depend on them)
    for name in ("db_gateway.py",):
        p = services_dir / name
        if not p.exists():
            logger.warning("[skip] %s not found", p)
            continue
        cmd = ["modal", "deploy"]
        if modal_env:
            cmd += ["--env", modal_env]
        cmd.append(str(p))
        if args.dry_run:
            logger.info("$ %s", " ".join(cmd))
            continue
        rc = run(cmd)
        code = code or rc

    # Deploy workers
    for name in (
        "label_conversations.py",
        "redact_conversation.py",
        "memory_ingest.py",
        "summarize_conversation.py",
    ):
        p = workers_dir / name
        if not p.exists():
            logger.warning("[skip] %s not found", p)
            continue
        cmd = ["modal", "deploy"]
        if modal_env:
            cmd += ["--env", modal_env]
        cmd.append(str(p))
        if args.dry_run:
            logger.info("$ %s", " ".join(cmd))
            continue
        rc = run(cmd)
        code = code or rc

    return code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
