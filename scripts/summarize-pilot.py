#!/usr/bin/env python3
"""Consolida os resumos do piloto usando apenas a biblioteca padrão."""
import csv
import json
import sys
from pathlib import Path


def main():
    directory = Path(sys.argv[1])
    fields = [
        "architecture", "rate", "repetition", "throughput", "p50", "p95", "p99",
        "error_rate", "dropped_iterations", "accepted_orders",
        "started_requests_per_second", "accepted_orders_per_second",
        "requests", "iterations", "duration_seconds", "observation_seconds",
        "timeouts", "server_errors", "pre_allocated_vus", "max_vus",
    ]
    summaries = [json.loads(path.read_text()) for path in directory.glob("rate*_run*.json")]
    summaries.sort(key=lambda row: (row["rate"], row["repetition"]))
    # Criação exclusiva: uma consolidação anterior também não é sobrescrita.
    with (directory / "consolidated.csv").open("x", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)


if __name__ == "__main__":
    main()
