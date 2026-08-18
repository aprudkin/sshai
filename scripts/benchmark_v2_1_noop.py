#!/usr/bin/env python3
"""Emit deterministic local control evidence for sshai benchmark v2.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys


OBSERVATION_RE = re.compile(r"^[LW][0-9]{2}$")


def comma_values(value: str) -> list[str]:
    values = value.split(",")
    if not values or any(not item for item in values):
        raise argparse.ArgumentTypeError("expected a non-empty comma-separated list")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--body-file", choices=("-",), required=True)
    parser.add_argument("--timeout", type=int, choices=(180,), required=True)
    parser.add_argument("--result-format", choices=("json",), required=True)
    parser.add_argument("--ctx")
    parser.add_argument("--delta", action="store_true")
    parser.add_argument("--branch", choices=("raw-control", "fanout-control"), required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--observations", type=comma_values, required=True)
    parser.add_argument("--hosts", type=comma_values, required=True)
    parser.add_argument("--expected-exits", type=comma_values, required=True)
    args = parser.parse_args()

    if not all(OBSERVATION_RE.fullmatch(item) for item in args.observations):
        parser.error("every observation must match LNN or WNN")
    if len({*args.observations}) != len(args.observations):
        parser.error("observations must be unique")
    if len(args.observations) != len(args.hosts) or len(args.hosts) != len(args.expected_exits):
        parser.error("observations, hosts, and expected exits must have equal lengths")
    try:
        expected_exits = [int(value) for value in args.expected_exits]
    except ValueError:
        parser.error("expected exits must be integers")

    body = sys.stdin.buffer.read()
    document = {
        "schema": "sshai-benchmark-noop/v2.1",
        "branch": args.branch,
        "call_id": args.call_id,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "results": [
            {
                "observation_id": observation,
                "host": host,
                "exit": expected_exit,
                "outcome": "success",
                "artifact_id": f"noop-{observation.lower()}",
                "artifact_path": "",
                "transport_error": "",
            }
            for observation, host, expected_exit in zip(
                args.observations, args.hosts, expected_exits, strict=True
            )
        ],
    }
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
