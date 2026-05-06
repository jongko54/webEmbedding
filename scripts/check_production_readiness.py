#!/usr/bin/env python3
"""Validate production-pipeline readiness gates for webEmbedding."""

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


def read_corpus(path: Path) -> list[str]:
    urls: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def require_keys(payload: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise AssertionError(f"{label} missing keys: {', '.join(missing)}")


def require_counter_keys(counter: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [key for key in keys if not counter.get(key)]
    if missing:
        raise AssertionError(f"{label} missing positive counts: {', '.join(missing)}")


def validate_gate_manifest(gates: dict[str, Any]) -> None:
    if gates.get("schema_version") != 1:
        raise AssertionError("production gates schema_version must be 1")
    priorities = gates.get("priorities")
    if not isinstance(priorities, list) or [item.get("id") for item in priorities if isinstance(item, dict)] != list(range(1, 8)):
        raise AssertionError("production gates must define priorities 1 through 7")
    require_keys(gates, ["corpus", "failure_taxonomy", "network_replay", "operations", "policy_guardrails"], "production gates")
    require_keys(gates["operations"], ["job_states", "retry_policy", "required_artifacts"], "operations gates")
    if len(gates["operations"].get("job_states") or []) < 6:
        raise AssertionError("operations gates must define production job states")
    if len(gates["policy_guardrails"].get("required_controls") or []) < 6:
        raise AssertionError("policy guardrails must define concrete controls")


def validate_corpus(corpus_urls: list[str], gates: dict[str, Any]) -> None:
    corpus_gate = gates.get("corpus", {})
    minimum_total = int(corpus_gate.get("minimum_total") or 0)
    minimum_fixtures = int(corpus_gate.get("minimum_deterministic_fixtures") or 0)
    fixture_count = sum(1 for url in corpus_urls if url.startswith("fixture://"))
    if len(corpus_urls) < minimum_total:
        raise AssertionError(f"corpus has {len(corpus_urls)} URLs, expected at least {minimum_total}")
    if fixture_count < minimum_fixtures:
        raise AssertionError(f"corpus has {fixture_count} deterministic fixtures, expected at least {minimum_fixtures}")


def validate_report(report: dict[str, Any], gates: dict[str, Any]) -> None:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise AssertionError("benchmark report missing summary")
    total = int(summary.get("total") or 0)
    ok_count = int((summary.get("status_counts") or {}).get("ok") or 0)
    complete_count = int((summary.get("route_quality_counts") or {}).get("complete") or 0)
    if total <= 0 or ok_count != total or complete_count != total:
        raise AssertionError(f"benchmark report not fully ok/complete: total={total} ok={ok_count} complete={complete_count}")

    corpus_gate = gates.get("corpus", {})
    require_counter_keys(summary.get("surface_counts") or {}, corpus_gate.get("required_surfaces") or [], "surface_counts")
    require_counter_keys(summary.get("renderer_route_counts") or {}, corpus_gate.get("required_routes") or [], "renderer_route_counts")
    require_counter_keys(summary.get("renderer_family_counts") or {}, corpus_gate.get("required_renderer_families") or [], "renderer_family_counts")
    require_counter_keys(summary.get("critical_depth_counts") or {}, corpus_gate.get("required_critical_depths") or [], "critical_depth_counts")

    taxonomy = gates.get("failure_taxonomy", {})
    failure_counts = summary.get("failure_code_counts") or {}
    require_counter_keys(failure_counts, taxonomy.get("required_positive_codes") or [], "failure_code_counts")
    blocked_present = [code for code in taxonomy.get("blocked_codes_must_be_absent") or [] if failure_counts.get(code)]
    if blocked_present:
        raise AssertionError(f"blocking failure codes present: {', '.join(blocked_present)}")


def validate_docs(root: Path) -> None:
    production_doc = root / "docs" / "production-pipeline.md"
    policy_doc = root / "docs" / "policy-and-safety-guardrails.md"
    if not production_doc.is_file():
        raise AssertionError("docs/production-pipeline.md is required")
    if not policy_doc.is_file():
        raise AssertionError("docs/policy-and-safety-guardrails.md is required")
    production_text = production_doc.read_text().lower()
    policy_text = policy_doc.read_text().lower()
    for token in ["queued", "retry", "artifact", "pipeline-run-manifest", "manual_review", "job queue", "har replay", "authenticated dashboard corpus"]:
        if token not in production_text:
            raise AssertionError(f"production pipeline doc missing `{token}`")
    for token in ["permission", "license", "robots", "tos", "pii", "session", "exact reuse", "bounded rebuild"]:
        if token not in policy_text:
            raise AssertionError(f"policy guardrail doc missing `{token}`")


def validate_ci_and_package(root: Path) -> None:
    package = load_json(root / "package.json")
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    files = package.get("files") if isinstance(package.get("files"), list) else []
    required_scripts = [
        "check:benchmark-routes:local",
        "check:benchmark-evidence:local",
        "check:clone-score-gate:local",
        "classify:pipeline-failures",
        "check:job-queue:local",
        "check:har-replay:local",
        "check:authenticated-corpus:local",
        "check:production-readiness:local",
    ]
    missing_scripts = [script for script in required_scripts if script not in scripts]
    if missing_scripts:
        raise AssertionError(f"package scripts missing: {', '.join(missing_scripts)}")
    required_files = [
        "scripts/classify_pipeline_failures.py",
        "scripts/check_production_readiness.py",
        "scripts/check_job_queue_smoke.py",
        "scripts/check_har_replay_smoke.py",
        "scripts/benchmark_authenticated_corpus.py",
        "docs/production-pipeline-gates.json",
        "docs/production-pipeline.md",
        "docs/authenticated-dashboard-corpus.example.json",
        "docs/policy-and-safety-guardrails.md",
    ]
    missing_files = [path for path in required_files if path not in files]
    if missing_files:
        raise AssertionError(f"package files missing: {', '.join(missing_files)}")
    workflow = (root / ".github" / "workflows" / "benchmark-regression.yml").read_text()
    for token in ["check_benchmark_evidence.py", "check_production_readiness.py", "check_job_queue_smoke.py", "check_har_replay_smoke.py", "benchmark_authenticated_corpus.py"]:
        if token not in workflow:
            raise AssertionError(f"benchmark regression workflow missing `{token}`")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate production readiness gates.")
    parser.add_argument("--report", default=".tmp/universal-route-benchmark/universal-route-report.json")
    parser.add_argument("--gates", default="docs/production-pipeline-gates.json")
    parser.add_argument("--corpus", default="docs/universal-benchmark-corpus.txt")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    gates = load_json(root / args.gates)
    report = load_json(root / args.report)
    corpus_urls = read_corpus(root / args.corpus)

    validate_gate_manifest(gates)
    validate_corpus(corpus_urls, gates)
    validate_report(report, gates)
    validate_docs(root)
    validate_ci_and_package(root)
    print("Production readiness check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
