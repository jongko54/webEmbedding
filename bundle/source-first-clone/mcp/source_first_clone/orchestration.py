"""High-level one-shot clone orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capture_bundle import capture_reference_bundle
from .failure_taxonomy import classify_pipeline_failure
from .reproduction import build_reproduction_bundle


def compact_capture_bundle(capture_bundle: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(capture_bundle))
    runtime = summary.get("runtime", {})
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}

    if isinstance(runtime.get("networkHits"), list):
        runtime["network_hit_count"] = len(runtime["networkHits"])
        runtime["network_hits_sample"] = runtime["networkHits"][:15]
        runtime.pop("networkHits", None)
    if isinstance(runtime.get("htmlMatches"), list):
        runtime["html_match_count"] = len(runtime["htmlMatches"])
        runtime["html_matches_sample"] = runtime["htmlMatches"][:15]
        runtime.pop("htmlMatches", None)

    for key in ("html", "dom", "accessibility", "styles", "network", "assets", "interactions", "interactionTrace"):
        capture = captures.get(key)
        if isinstance(capture, dict):
            capture.pop("content", None)

    screenshot_capture = captures.get("screenshot")
    if isinstance(screenshot_capture, dict):
        screenshot_capture.pop("base64", None)

    bundle = summary.get("bundle", {})
    captured_artifacts = bundle.get("captured_artifacts", {}) if isinstance(bundle, dict) else {}
    for artifact in captured_artifacts.values():
        if isinstance(artifact, dict):
            artifact.pop("content", None)
    breakpoint_summary = summary.get("breakpoints")
    if isinstance(breakpoint_summary, dict):
        variants = breakpoint_summary.get("variants")
        if isinstance(variants, list):
            breakpoint_summary["variant_count"] = len(variants)
            breakpoint_summary["variant_sample"] = variants[:3]
            breakpoint_summary.pop("variants", None)

    return summary


def _exact_reuse_ready(exact_reuse: Any) -> bool:
    if not isinstance(exact_reuse, dict):
        return False
    verification = exact_reuse.get("verification")
    if isinstance(verification, dict):
        return bool(
            verification.get("ready_for_exact_reuse")
            or verification.get("ready_for_exact_clone")
        )
    return False


def _write_pipeline_run_manifest(
    *,
    url: str,
    output_dir: str | None,
    capture_bundle: dict[str, Any],
    reproduction: dict[str, Any],
) -> dict[str, Any] | None:
    if not output_dir:
        return None
    output_root = Path(output_dir).expanduser().resolve()
    static = capture_bundle.get("static", {}) if isinstance(capture_bundle.get("static"), dict) else {}
    site_profile = static.get("site_profile", {}) if isinstance(static.get("site_profile"), dict) else {}
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle.get("runtime"), dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime.get("captures"), dict) else {}
    network_capture = captures.get("network", {}) if isinstance(captures.get("network"), dict) else {}
    network_summary = {}
    if isinstance(network_capture.get("content"), dict):
        network_summary = network_capture["content"].get("summary", {})
    failure_classification = classify_pipeline_failure(
        {
            "site_profile": site_profile,
            "route": site_profile.get("route_hints") if isinstance(site_profile.get("route_hints"), dict) else {},
            "policy": capture_bundle.get("policy", {}),
            "runtime": runtime,
            "captures": captures,
            "capture_summary": {
                "network": network_summary,
                "dom": captures.get("dom", {}) if isinstance(captures.get("dom"), dict) else {},
            },
            "evidence_limitations": reproduction.get("evidence_limitations"),
            "artifacts": (capture_bundle.get("bundle", {}) or {}).get("artifacts", {}),
        }
    )
    capture_persisted = (capture_bundle.get("bundle", {}) or {}).get("persisted", {})
    reproduction_persisted = reproduction.get("persisted", {}) if isinstance(reproduction.get("persisted"), dict) else {}
    manifest = {
        "schema_version": 1,
        "run_id": output_root.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": {
            "url": url,
            "final_url": static.get("final_url"),
        },
        "policy": capture_bundle.get("policy", {}),
        "route_hints": (site_profile.get("route_hints") if isinstance(site_profile.get("route_hints"), dict) else {}),
        "site_profile": {
            "primary_surface": site_profile.get("primary_surface"),
            "confidence": site_profile.get("confidence"),
            "signals": site_profile.get("signals"),
        },
        "result": {
            "coverage": reproduction.get("coverage"),
            "next_action": reproduction.get("next_action"),
            "exact_ready": _exact_reuse_ready(reproduction.get("exact_reuse")),
        },
        "failure_classification": failure_classification,
        "artifacts": {
            "capture": capture_persisted,
            "reproduction": reproduction_persisted,
        },
        "redaction_status": {
            "session_storage_state": "sensitive-artifact" if ((capture_bundle.get("bundle", {}) or {}).get("artifacts", {}) or {}).get("storage_state_exported") else "not-exported",
            "har_headers": "review-required",
            "cookies": "review-required",
            "query_strings": "review-required",
            "form_bodies": "review-required",
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "pipeline-run-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest["path"] = str(manifest_path)
    return manifest


def clone_reference_url(
    url: str,
    timeout_seconds: int = 20,
    wait_seconds: int = 8,
    user_data_dir: str | None = None,
    storage_state_path: str | None = None,
    storage_state_output_path: str | None = None,
    capture_html: bool = True,
    capture_screenshot: bool = True,
    viewport_width: int = 1440,
    viewport_height: int = 1200,
    breakpoint_profiles: list[str] | None = None,
    output_dir: str | None = None,
    exact_requested: bool = True,
    license_text: str | None = None,
    source_signals: list[str] | None = None,
    include_runtime_trace: bool = True,
) -> dict[str, Any]:
    capture_bundle = capture_reference_bundle(
        url=url,
        timeout_seconds=timeout_seconds,
        wait_seconds=wait_seconds,
        include_runtime_trace=include_runtime_trace,
        user_data_dir=user_data_dir,
        storage_state_path=storage_state_path,
        storage_state_output_path=storage_state_output_path,
        capture_html=capture_html,
        capture_screenshot=capture_screenshot,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        breakpoint_profiles=breakpoint_profiles,
        output_dir=output_dir,
        exact_requested=exact_requested,
        license_text=license_text,
        source_signals=source_signals,
    )
    reproduction = build_reproduction_bundle(
        capture_bundle=capture_bundle,
        output_dir=output_dir,
    )
    pipeline_run_manifest = _write_pipeline_run_manifest(
        url=url,
        output_dir=output_dir,
        capture_bundle=capture_bundle,
        reproduction=reproduction,
    )
    exact_reuse = reproduction.get("exact_reuse")
    return {
        "url": url,
        "policy_mode": capture_bundle.get("policy", {}).get("mode"),
        "next_action": reproduction.get("next_action"),
        "coverage": reproduction.get("coverage"),
        "exact_ready": _exact_reuse_ready(exact_reuse),
        "exact_reuse": exact_reuse,
        "pipeline_run_manifest": pipeline_run_manifest,
        "capture_bundle": compact_capture_bundle(capture_bundle),
        "reproduction": reproduction,
    }
