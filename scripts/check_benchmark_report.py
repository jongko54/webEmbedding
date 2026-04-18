#!/usr/bin/env python3
"""Validate a universal benchmark report against committed expectations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: str) -> Any:
    return json.loads(Path(path).expanduser().resolve().read_text())


def _get_path(payload: Any, dotted_path: str) -> Any:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def _validate_exact(payload: Any, expectations: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    for dotted_path, expected in expectations.items():
        try:
            actual = _get_path(payload, dotted_path)
        except KeyError:
            failures.append(f"{label}: missing `{dotted_path}` (expected `{expected}`)")
            continue
        if actual != expected:
            failures.append(
                f"{label}: `{dotted_path}` expected `{expected}` but got `{actual}`"
            )
    return failures


def _validate_absent_or_zero(payload: Any, paths: list[str], label: str) -> list[str]:
    failures: list[str] = []
    for dotted_path in paths:
        try:
            actual = _get_path(payload, dotted_path)
        except KeyError:
            continue
        if actual not in (0, None, {}, [], ""):
            failures.append(f"{label}: `{dotted_path}` should be absent or zero, got `{actual}`")
    return failures


def validate_report(report: dict[str, Any], expectations: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    expected_summary = expectations.get("summary", {}) if isinstance(expectations.get("summary"), dict) else {}
    failures.extend(
        _validate_exact(summary, expected_summary.get("exact", {}), "summary")
    )
    failures.extend(
        _validate_absent_or_zero(summary, expected_summary.get("absent_or_zero", []), "summary")
    )

    items = report.get("items", [])
    item_map = {
        item.get("url"): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    }
    expected_items = expectations.get("items", {}) if isinstance(expectations.get("items"), dict) else {}
    actual_urls = set(item_map)
    expected_urls = set(expected_items)
    missing_urls = sorted(expected_urls - actual_urls)
    unexpected_urls = sorted(actual_urls - expected_urls)
    if missing_urls:
        failures.append(f"items: missing expected URLs: {', '.join(missing_urls)}")
    if unexpected_urls:
        failures.append(f"items: unexpected URLs in report: {', '.join(unexpected_urls)}")

    for url, item_expectations in expected_items.items():
        item = item_map.get(url)
        if not isinstance(item, dict):
            continue
        exact = item_expectations.get("exact", {}) if isinstance(item_expectations, dict) else {}
        absent_or_zero = (
            item_expectations.get("absent_or_zero", [])
            if isinstance(item_expectations, dict)
            else []
        )
        failures.extend(_validate_exact(item, exact, url))
        failures.extend(_validate_absent_or_zero(item, absent_or_zero, url))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a universal benchmark report against expected routes.")
    parser.add_argument("--report", required=True, help="Path to universal-route-report.json")
    parser.add_argument("--expectations", required=True, help="Path to benchmark expectations JSON")
    args = parser.parse_args(argv)

    report = _load_json(args.report)
    expectations = _load_json(args.expectations)
    failures = validate_report(report, expectations)
    if failures:
        print("Benchmark regression check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    item_count = len(report.get("items", [])) if isinstance(report.get("items"), list) else 0
    print(
        "Benchmark regression check passed "
        f"for {item_count} URLs using {Path(args.expectations).name}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
