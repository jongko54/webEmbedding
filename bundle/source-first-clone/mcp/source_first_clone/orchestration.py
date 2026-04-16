"""High-level one-shot clone orchestration."""

from __future__ import annotations

import json
from typing import Any

from .capture_bundle import capture_reference_bundle
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
    exact_reuse = reproduction.get("exact_reuse")
    return {
        "url": url,
        "policy_mode": capture_bundle.get("policy", {}).get("mode"),
        "next_action": reproduction.get("next_action"),
        "coverage": reproduction.get("coverage"),
        "exact_ready": bool(exact_reuse),
        "exact_reuse": exact_reuse,
        "capture_bundle": compact_capture_bundle(capture_bundle),
        "reproduction": reproduction,
    }
