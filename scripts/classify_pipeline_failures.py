#!/usr/bin/env python3
"""Classify operational clone pipeline failures from reports or artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_classifier() -> Any:
    capture_root = repo_root() / "bundle" / "source-first-clone" / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.failure_taxonomy import classify_pipeline_failure

    return classify_pipeline_failure


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def contexts_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = report.get("items")
    if not isinstance(items, list):
        return []
    return [{"benchmark_item": item} for item in items if isinstance(item, dict)]


def context_from_capture(capture: dict[str, Any]) -> dict[str, Any]:
    runtime = capture.get("runtime", {}) if isinstance(capture.get("runtime"), dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime.get("captures"), dict) else {}
    static = capture.get("static", {}) if isinstance(capture.get("static"), dict) else {}
    profile = static.get("site_profile", {}) if isinstance(static.get("site_profile"), dict) else {}
    network_capture = captures.get("network", {}) if isinstance(captures.get("network"), dict) else {}
    network_summary = {}
    if isinstance(network_capture.get("content"), dict):
        network_summary = network_capture["content"].get("summary", {})
    return {
        "site_profile": profile,
        "route": profile.get("route_hints") if isinstance(profile.get("route_hints"), dict) else {},
        "policy": capture.get("policy", {}),
        "runtime": runtime,
        "captures": captures,
        "capture_summary": {
            "network": network_summary,
            "dom": captures.get("dom", {}),
        },
        "artifacts": (capture.get("bundle", {}) or {}).get("artifacts", {}),
    }


def summarize(classifications: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter: Counter[str] = Counter()
    code_counter: Counter[str] = Counter()
    issue_rows: list[dict[str, Any]] = []
    for index, classification in enumerate(classifications, start=1):
        status_counter[str(classification.get("status") or "unknown")] += 1
        code_counter.update(classification.get("codes") or [])
        for issue in classification.get("issues") or []:
            if isinstance(issue, dict):
                row = {"index": index}
                row.update(issue)
                issue_rows.append(row)
    return {
        "total": len(classifications),
        "status_counts": dict(status_counter),
        "code_counts": dict(code_counter),
        "issues": issue_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify operational failure/action codes for clone pipeline artifacts.")
    parser.add_argument("--report", help="Path to universal-route-report.json")
    parser.add_argument("--capture", help="Path to capture.json")
    parser.add_argument("--fail-on-blocked", action="store_true", help="Exit non-zero if any blocking status is present.")
    args = parser.parse_args(argv)

    if not args.report and not args.capture:
        raise SystemExit("Provide --report or --capture.")

    classify_pipeline_failure = load_classifier()
    contexts: list[dict[str, Any]] = []
    if args.report:
        contexts.extend(contexts_from_report(load_json(Path(args.report).expanduser().resolve())))
    if args.capture:
        contexts.append(context_from_capture(load_json(Path(args.capture).expanduser().resolve())))
    if not contexts:
        raise SystemExit("No classifiable contexts were found.")

    classifications = [classify_pipeline_failure(context) for context in contexts]
    summary = summarize(classifications)
    print(json.dumps(summary, indent=2))
    if args.fail_on_blocked and summary.get("status_counts", {}).get("blocked"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
