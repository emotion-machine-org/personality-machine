#!/usr/bin/env python3
"""
Run load tests and save results for historical tracking.

Usage:
    python run_test.py                    # Web UI (http://localhost:8089)
    python run_test.py --quick            # Web UI with quick settings (10 users, 1m)
    python run_test.py --headless         # Headless mode
    python run_test.py --headless --quick # Headless with quick settings
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
REPORTS_DIR = Path(__file__).parent / "reports"


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run_locust(
    users: int = 100,
    spawn_rate: int = 10,
    duration: str = "5m",
    host: str | None = None,
    tags: list[str] | None = None,
    headless: bool = False,
) -> tuple[int, str | None]:
    """Run locust. Returns (exit_code, csv_prefix)."""
    timestamp = get_timestamp()
    cmd = ["locust"]
    csv_prefix = None

    if headless:
        csv_prefix = str(RESULTS_DIR / f"loadtest_{timestamp}")
        html_report = str(REPORTS_DIR / f"report_{timestamp}.html")
        cmd.extend(
            [
                "--headless",
                "-u",
                str(users),
                "-r",
                str(spawn_rate),
                "--run-time",
                duration,
                "--csv",
                csv_prefix,
                "--html",
                html_report,
            ]
        )

    if host:
        cmd.extend(["--host", host])

    if tags:
        cmd.extend(["--tags", ",".join(tags)])

    RESULTS_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)

    result = subprocess.run(cmd, cwd=Path(__file__).parent, check=False)

    if headless and result.returncode == 0 and csv_prefix:
        create_summary(csv_prefix, timestamp, users, spawn_rate, duration, tags)

    return result.returncode, csv_prefix


def create_summary(
    csv_prefix: str,
    timestamp: str,
    users: int,
    spawn_rate: int,
    duration: str,
    tags: list[str] | None,
) -> None:
    """Create JSON summary from CSV results."""
    stats_file = Path(f"{csv_prefix}_stats.csv")
    if not stats_file.exists():
        return

    summary = {
        "timestamp": timestamp,
        "datetime": datetime.now().isoformat(),
        "config": {"users": users, "spawn_rate": spawn_rate, "duration": duration, "tags": tags},
        "endpoints": {},
        "totals": {},
    }

    with open(stats_file) as f:
        lines = f.readlines()
        if len(lines) < 2:
            return

        headers = lines[0].strip().split(",")
        for line in lines[1:]:
            values = line.strip().split(",")
            row = dict(zip(headers, values, strict=False))

            name = row.get("Name", "")
            if name == "Aggregated":
                summary["totals"] = {
                    "request_count": int(row.get("Request Count", 0)),
                    "failure_count": int(row.get("Failure Count", 0)),
                    "median_response_time": float(row.get("Median Response Time", 0)),
                    "avg_response_time": float(row.get("Average Response Time", 0)),
                    "p90": float(row.get("90%", 0)),
                    "p95": float(row.get("95%", 0)),
                    "p99": float(row.get("99%", 0)),
                    "requests_per_sec": float(row.get("Requests/s", 0)),
                }
            elif name:
                summary["endpoints"][name] = {
                    "request_count": int(row.get("Request Count", 0)),
                    "failure_count": int(row.get("Failure Count", 0)),
                    "median_response_time": float(row.get("Median Response Time", 0)),
                    "avg_response_time": float(row.get("Average Response Time", 0)),
                    "p95": float(row.get("95%", 0)),
                }

    summary_file = RESULTS_DIR / f"summary_{timestamp}.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary: {summary_file}")


def compare_results(current_file: str, baseline_file: str) -> None:
    """Compare two result files."""
    with open(current_file) as f:
        current = json.load(f)
    with open(baseline_file) as f:
        baseline = json.load(f)

    ct = current.get("totals", {})
    bt = baseline.get("totals", {})

    metrics = [
        ("Requests/s", "requests_per_sec", True),
        ("Avg Response (ms)", "avg_response_time", False),
        ("P95 (ms)", "p95", False),
        ("Failure Rate (%)", None, False),
    ]

    print(f"\n{'Metric':<25} {'Baseline':>12} {'Current':>12} {'Change':>12}")
    print("-" * 63)

    for label, key, higher_is_better in metrics:
        if key:
            b_val = bt.get(key, 0)
            c_val = ct.get(key, 0)
        else:
            b_val = (bt.get("failure_count", 0) / max(bt.get("request_count", 1), 1)) * 100
            c_val = (ct.get("failure_count", 0) / max(ct.get("request_count", 1), 1)) * 100

        pct_change = ((c_val - b_val) / b_val) * 100 if b_val > 0 else 0
        if higher_is_better:
            indicator = "+" if pct_change > 5 else ("-" if pct_change < -5 else "=")
        else:
            indicator = "+" if pct_change < -5 else ("-" if pct_change > 5 else "=")

        print(f"{label:<25} {b_val:>12.2f} {c_val:>12.2f} {pct_change:>+11.1f}% {indicator}")


def main():
    parser = argparse.ArgumentParser(description="Run load tests")
    parser.add_argument("--headless", action="store_true", help="Run without web UI")
    parser.add_argument("-u", "--users", type=int, default=100, help="Number of users")
    parser.add_argument("-r", "--spawn-rate", type=int, default=10, help="Spawn rate")
    parser.add_argument("-t", "--duration", default="5m", help="Duration (e.g., 5m, 1h)")
    parser.add_argument("--host", help="Target host URL")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--quick", action="store_true", help="Quick settings (10 users, 1m)")
    parser.add_argument("--compare", help="Compare with baseline file")
    parser.add_argument("--baseline", action="store_true", help="Save as baseline")

    args = parser.parse_args()

    if args.quick:
        args.users = 10
        args.spawn_rate = 5
        args.duration = "1m"

    tags = args.tags.split(",") if args.tags else None

    exit_code, _ = run_locust(
        users=args.users,
        spawn_rate=args.spawn_rate,
        duration=args.duration,
        host=args.host,
        tags=tags,
        headless=args.headless,
    )

    if args.baseline and exit_code == 0:
        summaries = sorted(RESULTS_DIR.glob("summary_*.json"), reverse=True)
        if summaries:
            baseline_path = RESULTS_DIR / "baseline.json"
            with open(summaries[0]) as f:
                data = json.load(f)
            with open(baseline_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Baseline: {baseline_path}")

    if args.compare and exit_code == 0:
        summaries = sorted(RESULTS_DIR.glob("summary_*.json"), reverse=True)
        if summaries:
            compare_results(str(summaries[0]), args.compare)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
