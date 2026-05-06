#!/usr/bin/env python3
"""Validate committed benchmark evidence metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return payload


def require_string(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"{label}: missing non-empty `{key}`")
    return value


def require_number(payload: dict[str, Any], key: str, label: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AssertionError(f"{label}: missing numeric `{key}`")
    return float(value)


def validate_route_regression(evidence: dict[str, Any]) -> None:
    route = evidence.get("route_regression")
    if not isinstance(route, dict):
        raise AssertionError("missing route_regression object")
    for key in ("command", "report_path", "expectations", "corpus"):
        require_string(route, key, "route_regression")
    expected = route.get("expected")
    if not isinstance(expected, dict):
        raise AssertionError("route_regression.expected must be an object")
    total = require_number(expected, "total", "route_regression.expected")
    status_ok = require_number(expected, "status_ok", "route_regression.expected")
    complete = require_number(expected, "route_quality_complete", "route_regression.expected")
    if total < 1:
        raise AssertionError("route_regression.expected.total must be positive")
    if status_ok < total or complete < total:
        raise AssertionError("route regression expected counts must cover every corpus item")
    fixtures = expected.get("deterministic_fixtures")
    if not isinstance(fixtures, list) or "fixture://public-app-gate" not in fixtures:
        raise AssertionError("route regression must include fixture://public-app-gate")
    if len(fixtures) < 20:
        raise AssertionError("route regression must include at least 20 deterministic fixtures for production coverage")
    evidence_limits = expected.get("positive_evidence_limits")
    if not isinstance(evidence_limits, list) or "public-web-app-gate" not in evidence_limits:
        raise AssertionError("route regression must positively cover public-web-app-gate")
    failure_codes = expected.get("positive_failure_codes")
    required_codes = {
        "network-replay-limited",
        "auth-session-missing",
        "public-app-gate",
        "native-app-target-required",
        "canvas-visual-fallback",
    }
    if not isinstance(failure_codes, list) or not required_codes.issubset(set(failure_codes)):
        raise AssertionError("route regression must positively cover production failure/action taxonomy codes")


def validate_clone_checkpoint(item: dict[str, Any], index: int) -> None:
    label = require_string(item, "label", f"clone_quality_checkpoints[{index}]")
    require_string(item, "url", label)
    require_string(item, "command", label)
    require_string(item, "path", label)
    if item.get("ready_for_exact_reuse") is True:
        return
    scores = item.get("scores")
    minimums = item.get("minimums")
    if not isinstance(scores, dict) or not scores:
        raise AssertionError(f"{label}: missing scores object")
    if not isinstance(minimums, dict) or not minimums:
        raise AssertionError(f"{label}: missing minimums object")
    for key, expected_minimum in minimums.items():
        if not isinstance(expected_minimum, (int, float)) or isinstance(expected_minimum, bool):
            raise AssertionError(f"{label}: minimum `{key}` must be numeric")
        actual = scores.get(key)
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            raise AssertionError(f"{label}: score `{key}` must be numeric")
        if actual < expected_minimum:
            raise AssertionError(f"{label}: score `{key}`={actual} below minimum {expected_minimum}")


def validate_evidence(evidence: dict[str, Any]) -> None:
    if evidence.get("schema_version") != 1:
        raise AssertionError("schema_version must be 1")
    require_string(evidence, "updated", "benchmark evidence")
    validate_route_regression(evidence)
    checkpoints = evidence.get("clone_quality_checkpoints")
    if not isinstance(checkpoints, list) or len(checkpoints) < 3:
        raise AssertionError("expected at least three clone_quality_checkpoints")
    for index, item in enumerate(checkpoints):
        if not isinstance(item, dict):
            raise AssertionError(f"clone_quality_checkpoints[{index}] must be an object")
        validate_clone_checkpoint(item, index)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate committed benchmark evidence metadata.")
    parser.add_argument(
        "--evidence",
        default="docs/benchmark-evidence.json",
        help="Path to benchmark evidence JSON.",
    )
    args = parser.parse_args(argv)

    evidence_path = Path(args.evidence).expanduser().resolve()
    validate_evidence(load_json(evidence_path))
    print(f"Benchmark evidence check passed: {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
