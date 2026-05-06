#!/usr/bin/env python3
"""Summarize webEmbedding telemetry JSONL events."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any


def nested_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def string_value(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def event_command(payload: dict[str, Any]) -> str | None:
    return string_value(payload.get("command")) or string_value(nested_get(payload, ("properties", "command")))


def event_version(payload: dict[str, Any]) -> str | None:
    return (
        string_value(payload.get("version"))
        or string_value(nested_get(payload, ("app", "version")))
        or string_value(nested_get(payload, ("package", "version")))
    )


def summarize(path: Path) -> dict[str, Any]:
    event_count = 0
    invalid_lines = 0
    anonymous_ids: set[str] = set()
    event_counts: collections.Counter[str] = collections.Counter()
    command_counts: collections.Counter[str] = collections.Counter()
    version_counts: collections.Counter[str] = collections.Counter()

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(payload, dict):
                invalid_lines += 1
                continue

            event_count += 1
            anonymous_id = string_value(payload.get("anonymous_id"))
            if anonymous_id:
                anonymous_ids.add(anonymous_id)

            event_name = string_value(payload.get("event")) or "unknown"
            event_counts[event_name] += 1

            command = event_command(payload) or "unknown"
            command_counts[command] += 1

            version = event_version(payload) or "unknown"
            version_counts[version] += 1

    return {
        "events": event_count,
        "invalid_lines": invalid_lines,
        "unique_anonymous_ids": len(anonymous_ids),
        "install_executions": command_counts.get("install", 0),
        "clone_executions": command_counts.get("clone", 0),
        "command_executions": sum(
            count for command, count in command_counts.items() if command != "unknown"
        ),
        "events_by_name": dict(event_counts.most_common()),
        "commands": dict(command_counts.most_common()),
        "versions": dict(version_counts.most_common()),
    }


def print_counter(title: str, values: dict[str, int]) -> None:
    print(f"{title}:")
    if not values:
        print("  (none)")
        return
    for key, count in values.items():
        print(f"  {key}: {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize webEmbedding telemetry JSONL.")
    parser.add_argument("jsonl", help="Path to telemetry JSONL file.")
    parser.add_argument("--json", action="store_true", help="Print summary as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.jsonl).expanduser().resolve()
    if not path.exists():
        print(f"Telemetry JSONL not found: {path}", file=sys.stderr)
        return 2

    summary = summarize(path)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"events: {summary['events']}")
    print(f"invalid_lines: {summary['invalid_lines']}")
    print(f"unique_anonymous_ids: {summary['unique_anonymous_ids']}")
    print(f"install_executions: {summary['install_executions']}")
    print(f"clone_executions: {summary['clone_executions']}")
    print(f"command_executions: {summary['command_executions']}")
    print_counter("events_by_name", summary["events_by_name"])
    print_counter("commands", summary["commands"])
    print_counter("versions", summary["versions"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
