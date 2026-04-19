#!/usr/bin/env python3
"""Validate local frame/shadow interaction parity fixtures when they exist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_PATH = REPO_ROOT / "bundle" / "source-first-clone" / "mcp"
sys.path.insert(0, str(MCP_PATH))

from source_first_clone.verification_support import build_fidelity_report  # noqa: E402


DEFAULT_REFERENCE = REPO_ROOT / ".tmp" / "frame-shadow-fixture-smoke" / "capture.json"
DEFAULT_CANDIDATE = REPO_ROOT / ".tmp" / "frame-shadow-fixture-smoke-current" / "capture.json"
EXPECTED_ROOT_KINDS = {"document", "frame-document", "shadow-root"}
ROOT_OVERLAP_KEYS = (
    "root_context_overlap",
    "frame_source_overlap",
    "frame_url_overlap",
    "surface_index_overlap",
    "root_signature_overlap",
    "root_path_overlap",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _interaction_payload(bundle: dict[str, Any], key: str) -> Any:
    captures = ((bundle.get("runtime") or {}).get("captures") or {}) if isinstance(bundle, dict) else {}
    payload = captures.get(key) if isinstance(captures, dict) else {}
    return payload.get("content") if isinstance(payload, dict) else None


def _collect_root_kinds(bundle: dict[str, Any]) -> set[str]:
    kinds: set[str] = set()
    interactions = _interaction_payload(bundle, "interactions")
    if isinstance(interactions, list):
        for entry in interactions:
            if isinstance(entry, dict):
                context = entry.get("rootContext") if isinstance(entry.get("rootContext"), dict) else {}
                if context.get("kind"):
                    kinds.add(str(context["kind"]))

    trace = _interaction_payload(bundle, "interactionTrace")
    steps = trace.get("steps", []) if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []
    executions = trace.get("executions", []) if isinstance(trace, dict) and isinstance(trace.get("executions"), list) else []
    for entry in [*steps, *executions]:
        if isinstance(entry, dict):
            context = entry.get("rootContext") if isinstance(entry.get("rootContext"), dict) else {}
            if context.get("kind"):
                kinds.add(str(context["kind"]))
    return kinds


def _check_by_name(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    for detail in report.get("check_details", []):
        if isinstance(detail, dict) and detail.get("name") == name:
            return detail
    return None


def _require_exact(
    failures: list[str],
    check: dict[str, Any],
    detail_key: str,
    expected: Any,
) -> None:
    details = check.get("details", {}) if isinstance(check.get("details"), dict) else {}
    actual = details.get(detail_key)
    if actual != expected:
        failures.append(f"{check.get('name')}: {detail_key} expected {expected!r}, got {actual!r}")


def validate_root_interaction_parity(reference_bundle: dict[str, Any], candidate_bundle: dict[str, Any]) -> list[str]:
    report = build_fidelity_report(reference_bundle=reference_bundle, candidate_bundle=candidate_bundle)
    failures: list[str] = []

    for label, bundle in (("reference", reference_bundle), ("candidate", candidate_bundle)):
        kinds = _collect_root_kinds(bundle)
        missing = sorted(EXPECTED_ROOT_KINDS - kinds)
        if missing:
            failures.append(f"{label}: missing rootContext kinds: {', '.join(missing)}")

    states = _check_by_name(report, "interaction states")
    trace = _check_by_name(report, "interaction trace")
    if not states:
        failures.append("missing interaction states check")
    elif states.get("status") != "present":
        failures.append(f"interaction states: expected present, got {states.get('status')!r}")
    else:
        _require_exact(failures, states, "entry_count_delta", 0)
        for key in ROOT_OVERLAP_KEYS:
            _require_exact(failures, states, key, 1.0)

    if not trace:
        failures.append("missing interaction trace check")
    elif trace.get("status") != "present":
        failures.append(f"interaction trace: expected present, got {trace.get('status')!r}")
    else:
        _require_exact(failures, trace, "step_count_delta", 0)
        _require_exact(failures, trace, "executed_count_delta", 0)
        for key in ROOT_OVERLAP_KEYS:
            _require_exact(failures, trace, key, 1.0)

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local frame/shadow root-aware interaction parity fixtures.")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE, help="Reference capture.json path")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE, help="Candidate capture.json path")
    args = parser.parse_args(argv)

    missing = [path for path in (args.reference, args.candidate) if not path.exists()]
    if missing:
        print("Root interaction parity check skipped; local fixture artifacts are missing:")
        for path in missing:
            print(f"- {path}")
        return 0

    failures = validate_root_interaction_parity(_load_json(args.reference), _load_json(args.candidate))
    if failures:
        print("Root interaction parity check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "Root interaction parity check passed for local frame/shadow fixtures "
        f"({args.reference.parent.name} -> {args.candidate.parent.name})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
