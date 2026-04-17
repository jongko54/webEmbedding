"""Canonical capture bundle scaffolding."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any

from .acquisition import discover_embed_candidates, inspect_reference, trace_runtime_sources
from .constants import BREAKPOINT_PROFILES, CAPTURE_SCHEMA_VERSION
from .policy import classify_clone_mode


def capture_reference_bundle(
    url: str,
    timeout_seconds: int = 20,
    wait_seconds: int = 8,
    include_runtime_trace: bool = True,
    user_data_dir: str | None = None,
    storage_state_path: str | None = None,
    storage_state_output_path: str | None = None,
    capture_html: bool = False,
    capture_screenshot: bool = False,
    viewport_width: int = 1440,
    viewport_height: int = 1200,
    breakpoint_profiles: list[str] | None = None,
    output_dir: str | None = None,
    exact_requested: bool = True,
    license_text: str | None = None,
    source_signals: list[str] | None = None,
) -> dict[str, Any]:
    static = inspect_reference(url, timeout_seconds=timeout_seconds)
    candidates = discover_embed_candidates(url, timeout_seconds=timeout_seconds)["candidates"]
    merged_source_signals = list(
        dict.fromkeys(
            [
                *(str(item).lower() for item in (source_signals or []) if item),
                *(str(item).lower() for item in (static.get("source_signals") or []) if item),
            ]
        )
    )
    policy = classify_clone_mode(
        exact_requested=exact_requested,
        license_text=license_text,
        candidates=candidates,
        source_signals=merged_source_signals,
        site_profile=static.get("site_profile"),
    )
    runtime_output_dir = Path(output_dir).expanduser().resolve() if output_dir else None
    breakpoint_requests = _resolve_breakpoint_requests(
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        breakpoint_profiles=breakpoint_profiles,
    )

    bundle_payload = _build_capture_bundle(
        url=url,
        static=static,
        policy=policy,
        source_signals=merged_source_signals,
        wait_seconds=wait_seconds,
        include_runtime_trace=include_runtime_trace,
        user_data_dir=user_data_dir,
        storage_state_path=storage_state_path,
        storage_state_output_path=storage_state_output_path,
        capture_html=capture_html,
        capture_screenshot=capture_screenshot,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        runtime_output_dir=runtime_output_dir,
        breakpoint_name="primary",
    )

    if breakpoint_requests:
        variant_summaries: list[dict[str, Any]] = []
        for request in breakpoint_requests:
            variant_output_dir = runtime_output_dir / "breakpoints" / request["name"] if runtime_output_dir else None
            variant_bundle = _build_capture_bundle(
                url=url,
                static=static,
                policy=policy,
                source_signals=merged_source_signals,
                wait_seconds=wait_seconds,
                include_runtime_trace=include_runtime_trace,
                user_data_dir=user_data_dir,
                storage_state_path=storage_state_path,
                storage_state_output_path=None,
                capture_html=capture_html,
                capture_screenshot=capture_screenshot,
                viewport_width=request["width"],
                viewport_height=request["height"],
                runtime_output_dir=variant_output_dir,
                breakpoint_name=request["name"],
            )
            if variant_output_dir:
                variant_persisted = persist_capture_bundle(variant_output_dir, variant_bundle)
                variant_bundle["bundle"]["persisted"] = variant_persisted
            variant_summaries.append(_summarize_breakpoint_variant(request["name"], variant_bundle))

        fully_captured = all(summary.get("available") for summary in variant_summaries)
        bundle_payload["breakpoints"] = {
            "requested_profiles": [request["name"] for request in breakpoint_requests],
            "primary": {
                "name": "primary",
                "width": viewport_width,
                "height": viewport_height,
            },
            "captured_count": sum(1 for summary in variant_summaries if summary.get("available")),
            "variants": variant_summaries,
        }
        bundle_payload["bundle"]["artifacts"]["breakpoint_variants"] = fully_captured
        bundle_payload["bundle"]["captured_artifacts"]["breakpoints"] = {
            "available": bool(variant_summaries),
            "count": len(variant_summaries),
            "captured_count": sum(1 for summary in variant_summaries if summary.get("available")),
            "variants": variant_summaries,
        }
        if not fully_captured and "breakpoint viewport set" not in bundle_payload["bundle"]["missing_artifacts"]:
            bundle_payload["bundle"]["missing_artifacts"].append("breakpoint viewport set")

    if runtime_output_dir:
        persisted = persist_capture_bundle(runtime_output_dir, bundle_payload)
        bundle_payload["bundle"]["persisted"] = persisted

    return bundle_payload


def _resolve_breakpoint_requests(
    viewport_width: int,
    viewport_height: int,
    breakpoint_profiles: list[str] | None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_sizes = {(int(viewport_width), int(viewport_height))}
    for raw_profile in breakpoint_profiles or []:
        profile_name = str(raw_profile or "").strip().lower()
        profile = BREAKPOINT_PROFILES.get(profile_name)
        if not profile or profile_name in seen_names:
            continue
        width = int(profile["width"])
        height = int(profile["height"])
        if (width, height) in seen_sizes:
            continue
        requests.append({"name": profile_name, "width": width, "height": height})
        seen_names.add(profile_name)
        seen_sizes.add((width, height))
    return requests


def _build_capture_bundle(
    *,
    url: str,
    static: dict[str, Any],
    policy: dict[str, Any],
    source_signals: list[str],
    wait_seconds: int,
    include_runtime_trace: bool,
    user_data_dir: str | None,
    storage_state_path: str | None,
    storage_state_output_path: str | None,
    capture_html: bool,
    capture_screenshot: bool,
    viewport_width: int,
    viewport_height: int,
    runtime_output_dir: Path | None,
    breakpoint_name: str,
) -> dict[str, Any]:
    derived_storage_state_output_path = storage_state_output_path
    if runtime_output_dir and not derived_storage_state_output_path:
        derived_storage_state_output_path = str(runtime_output_dir / "session" / "storage-state.json")

    runtime_request = {
        "user_data_dir": user_data_dir,
        "storage_state_path": storage_state_path,
        "storage_state_output_path": derived_storage_state_output_path,
        "capture_html": capture_html,
        "capture_screenshot": capture_screenshot,
        "viewport_width": viewport_width,
        "viewport_height": viewport_height,
        "output_dir": str(runtime_output_dir) if runtime_output_dir else None,
        "breakpoint_name": breakpoint_name,
    }
    runtime = (
        trace_runtime_sources(
            url,
            wait_seconds=wait_seconds,
            user_data_dir=user_data_dir,
            storage_state_path=storage_state_path,
            storage_state_output_path=derived_storage_state_output_path,
            capture_html=capture_html,
            capture_screenshot=capture_screenshot,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
        if include_runtime_trace
        else {"available": False, "skipped": True, "session": runtime_request}
    )
    capture_state = runtime.get("captures", {}) if runtime.get("available") else {}
    html_capture = capture_state.get("html", {}) if isinstance(capture_state, dict) else {}
    screenshot_capture = capture_state.get("screenshot", {}) if isinstance(capture_state, dict) else {}
    dom_capture = capture_state.get("dom", {}) if isinstance(capture_state, dict) else {}
    accessibility_capture = capture_state.get("accessibility", {}) if isinstance(capture_state, dict) else {}
    styles_capture = capture_state.get("styles", {}) if isinstance(capture_state, dict) else {}
    css_analysis_capture = capture_state.get("cssAnalysis", {}) if isinstance(capture_state, dict) else {}
    network_capture = capture_state.get("network", {}) if isinstance(capture_state, dict) else {}
    assets_capture = capture_state.get("assets", {}) if isinstance(capture_state, dict) else {}
    interactions_capture = capture_state.get("interactions", {}) if isinstance(capture_state, dict) else {}
    interaction_trace_capture = capture_state.get("interactionTrace", {}) if isinstance(capture_state, dict) else {}
    runtime_session = runtime.get("session", {}) if isinstance(runtime, dict) else {}
    gaps = [
        "DOM snapshot",
        "accessibility tree",
        "computed styles",
        "stylesheet analysis",
        "network manifest",
        "interaction states",
        "interaction replay trace",
        "asset manifest",
        "viewport screenshot set",
        "exported storage state",
    ]
    if dom_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "DOM snapshot"]
    if accessibility_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "accessibility tree"]
    if styles_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "computed styles"]
    if css_analysis_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "stylesheet analysis"]
    if network_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "network manifest"]
    if assets_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "asset manifest"]
    if interactions_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "interaction states"]
    if interaction_trace_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "interaction replay trace"]
    if screenshot_capture.get("available"):
        gaps = [gap for gap in gaps if gap != "viewport screenshot set"]
    if runtime_session.get("storageStateExported"):
        gaps = [gap for gap in gaps if gap != "exported storage state"]

    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "url": url,
        "session_request": runtime_request,
        "static": static,
        "source_signals": source_signals,
        "runtime": runtime,
        "policy": policy,
        "bundle": {
            "artifacts": {
                "html": bool(html_capture.get("available")),
                "html_requested": capture_html,
                "screenshot": bool(screenshot_capture.get("available")),
                "screenshot_requested": capture_screenshot,
                "storage_state_exported": bool(runtime_session.get("storageStateExported")),
                "storage_state_output_requested": bool(derived_storage_state_output_path),
                "dom_snapshot": bool(dom_capture.get("available")),
                "accessibility_tree": bool(accessibility_capture.get("available")),
                "computed_styles": bool(styles_capture.get("available")),
                "css_analysis": bool(css_analysis_capture.get("available")),
                "network_manifest": bool(network_capture.get("available")),
                "assets": bool(assets_capture.get("available")),
                "interaction_states": bool(interactions_capture.get("available")),
                "interaction_trace": bool(interaction_trace_capture.get("available")),
            },
            "missing_artifacts": gaps,
            "captured_artifacts": {
                "dom": dom_capture if dom_capture.get("available") else None,
                "accessibility": accessibility_capture if accessibility_capture.get("available") else None,
                "styles": styles_capture if styles_capture.get("available") else None,
                "css_analysis": css_analysis_capture if css_analysis_capture.get("available") else None,
                "network": network_capture if network_capture.get("available") else None,
                "assets": assets_capture if assets_capture.get("available") else None,
                "interactions": interactions_capture if interactions_capture.get("available") else None,
                "interaction_trace": interaction_trace_capture if interaction_trace_capture.get("available") else None,
                "html": html_capture if html_capture.get("available") else None,
                "screenshot": screenshot_capture if screenshot_capture.get("available") else None,
                "session": runtime_session if runtime_session else None,
            },
        },
        "next_step": "capture more state before attempting exact reproduction",
        "note": "This is a scaffolded capture bundle. It intentionally does not claim full DOM/CSS fidelity yet, even when Playwright session capture is available.",
    }


def _summarize_breakpoint_variant(name: str, bundle_payload: dict[str, Any]) -> dict[str, Any]:
    runtime = bundle_payload.get("runtime", {}) if isinstance(bundle_payload, dict) else {}
    session_request = bundle_payload.get("session_request", {}) if isinstance(bundle_payload, dict) else {}
    bundle = bundle_payload.get("bundle", {}) if isinstance(bundle_payload, dict) else {}
    persisted = bundle.get("persisted", {}) if isinstance(bundle, dict) else {}
    persisted_files = persisted.get("files", {}) if isinstance(persisted, dict) else {}
    static = bundle_payload.get("static", {}) if isinstance(bundle_payload, dict) else {}
    return {
        "name": name,
        "available": bool(runtime.get("available")),
        "viewport": {
            "width": session_request.get("viewport_width"),
            "height": session_request.get("viewport_height"),
        },
        "title": runtime.get("title") or static.get("title"),
        "final_url": runtime.get("finalUrl") or static.get("final_url"),
        "artifacts": bundle.get("artifacts"),
        "persisted_root": persisted.get("root"),
        "capture_manifest": persisted_files.get("capture_manifest"),
        "screenshot": persisted_files.get("screenshot"),
    }


def persist_capture_bundle(output_dir: Path, bundle_payload: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["static", "runtime", "dom", "screenshots", "session", "provenance", "accessibility", "styles", "network", "assets", "interactions"]:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    persisted: dict[str, Any] = {
        "root": str(output_dir),
        "files": {},
    }

    static_path = output_dir / "static" / "inspect.json"
    static_path.write_text(json.dumps(bundle_payload["static"], indent=2) + "\n")
    persisted["files"]["static_inspect"] = str(static_path)

    policy_path = output_dir / "provenance" / "policy.json"
    policy_path.write_text(json.dumps(bundle_payload["policy"], indent=2) + "\n")
    persisted["files"]["policy"] = str(policy_path)

    runtime = bundle_payload.get("runtime", {})
    runtime_capture = runtime.get("captures", {}) if isinstance(runtime, dict) else {}

    if runtime:
        runtime_json = output_dir / "runtime" / "trace.json"
        runtime_json.write_text(json.dumps(runtime, indent=2) + "\n")
        persisted["files"]["runtime_trace"] = str(runtime_json)

    dom_capture = runtime_capture.get("dom", {}) if isinstance(runtime_capture, dict) else {}
    if dom_capture.get("available") and dom_capture.get("content") is not None:
        dom_path = output_dir / "dom" / "snapshot.json"
        dom_path.write_text(json.dumps(dom_capture["content"], indent=2) + "\n")
        persisted["files"]["dom_snapshot"] = str(dom_path)
        bundle_payload["bundle"]["captured_artifacts"]["dom"] = {
            "available": True,
            "node_count_approx": dom_capture.get("nodeCountApprox"),
            "node_count": dom_capture.get("nodeCount"),
            "shadow_root_count": dom_capture.get("shadowRootCount"),
            "frame_document_count": dom_capture.get("frameDocumentCount"),
            "inaccessible_frame_count": dom_capture.get("inaccessibleFrameCount"),
            "path": str(dom_path),
        }

    accessibility_capture = runtime_capture.get("accessibility", {}) if isinstance(runtime_capture, dict) else {}
    if accessibility_capture.get("available") and accessibility_capture.get("content") is not None:
        accessibility_path = output_dir / "accessibility" / "tree.json"
        accessibility_path.write_text(json.dumps(accessibility_capture["content"], indent=2) + "\n")
        persisted["files"]["accessibility_tree"] = str(accessibility_path)
        bundle_payload["bundle"]["captured_artifacts"]["accessibility"] = {
            "available": True,
            "path": str(accessibility_path),
        }

    styles_capture = runtime_capture.get("styles", {}) if isinstance(runtime_capture, dict) else {}
    if styles_capture.get("available") and styles_capture.get("content") is not None:
        styles_path = output_dir / "styles" / "computed-summary.json"
        styles_path.write_text(json.dumps(styles_capture["content"], indent=2) + "\n")
        persisted["files"]["computed_styles"] = str(styles_path)
        bundle_payload["bundle"]["captured_artifacts"]["styles"] = {
            "available": True,
            "entry_count": styles_capture.get("entryCount"),
            "path": str(styles_path),
        }

    css_analysis_capture = runtime_capture.get("cssAnalysis", {}) if isinstance(runtime_capture, dict) else {}
    if css_analysis_capture.get("available") and css_analysis_capture.get("content") is not None:
        css_analysis_path = output_dir / "styles" / "css-analysis.json"
        css_analysis_path.write_text(json.dumps(css_analysis_capture["content"], indent=2) + "\n")
        persisted["files"]["css_analysis"] = str(css_analysis_path)
        bundle_payload["bundle"]["captured_artifacts"]["css_analysis"] = {
            "available": True,
            "stylesheet_count": css_analysis_capture.get("stylesheetCount"),
            "accessible_stylesheet_count": css_analysis_capture.get("accessibleStylesheetCount"),
            "linked_stylesheet_count": css_analysis_capture.get("linkedStylesheetCount"),
            "inline_style_tag_count": css_analysis_capture.get("inlineStyleTagCount"),
            "style_attribute_count": css_analysis_capture.get("styleAttributeCount"),
            "preload_link_count": css_analysis_capture.get("preloadLinkCount"),
            "font_face_rule_count": css_analysis_capture.get("fontFaceRuleCount"),
            "path": str(css_analysis_path),
        }

    network_capture = runtime_capture.get("network", {}) if isinstance(runtime_capture, dict) else {}
    if network_capture.get("available") and network_capture.get("content") is not None:
        network_path = output_dir / "network" / "manifest.json"
        network_content = network_capture["content"] if isinstance(network_capture.get("content"), dict) else {}
        network_path.write_text(json.dumps(network_content, indent=2) + "\n")
        persisted["files"]["network_manifest"] = str(network_path)
        network_summary = network_content.get("summary") if isinstance(network_content.get("summary"), dict) else {}
        har_like = network_content.get("harLike") if isinstance(network_content.get("harLike"), dict) else None
        har_standard = network_content.get("har") if isinstance(network_content.get("har"), dict) else None
        har_path = None
        har_like_path = None
        if har_standard is not None:
            har_path = output_dir / "network" / "har.json"
            har_path.write_text(json.dumps(har_standard, indent=2) + "\n")
            persisted["files"]["network_har"] = str(har_path)
        elif har_like is not None:
            har_path = output_dir / "network" / "har.json"
            har_fallback = _standardize_har_like(har_like)
            har_path.write_text(json.dumps(har_fallback, indent=2) + "\n")
            persisted["files"]["network_har"] = str(har_path)
        if har_like is not None:
            har_like_path = output_dir / "network" / "har-like.json"
            har_like_path.write_text(json.dumps(har_like, indent=2) + "\n")
            persisted["files"]["network_har_like"] = str(har_like_path)
        bundle_payload["bundle"]["captured_artifacts"]["network"] = {
            "available": True,
            "request_count": network_capture.get("requestCount"),
            "response_count": network_capture.get("responseCount"),
            "failure_count": network_capture.get("failureCount"),
            "frame_url_count": network_capture.get("frameUrlCount"),
            "redirect_count": network_summary.get("redirectCount"),
            "redirect_sample_count": len(network_summary.get("redirectSample") or []),
            "request_header_presence_summary": network_summary.get("requestHeaderPresenceSummary"),
            "response_header_presence_summary": network_summary.get("responseHeaderPresenceSummary"),
            "timing_bucket_counts": network_summary.get("timingBucketCounts"),
            "response_body_availability": network_summary.get("responseBodyAvailability"),
            "page_timings": network_summary.get("pageTimings"),
            "har_export_path": str(har_path) if har_path else None,
            "har_page_count": har_standard.get("summary", {}).get("pageCount") if har_standard else None,
            "har_entry_count": har_standard.get("summary", {}).get("entryCount") if har_standard else None,
            "har_like_entry_count": har_like.get("summary", {}).get("entryCount") if har_like else None,
            "har_like_page_count": har_like.get("summary", {}).get("pageCount") if har_like else None,
            "har_like_path": str(har_like_path) if har_like_path else None,
            "path": str(network_path),
        }

    assets_capture = runtime_capture.get("assets", {}) if isinstance(runtime_capture, dict) else {}
    if assets_capture.get("available") and assets_capture.get("content") is not None:
        assets_path = output_dir / "assets" / "inventory.json"
        assets_path.write_text(json.dumps(assets_capture["content"], indent=2) + "\n")
        persisted["files"]["asset_inventory"] = str(assets_path)
        bundle_payload["bundle"]["captured_artifacts"]["assets"] = {
            "available": True,
            "summary": assets_capture.get("summary"),
            "path": str(assets_path),
        }

    interactions_capture = runtime_capture.get("interactions", {}) if isinstance(runtime_capture, dict) else {}
    if interactions_capture.get("available") and interactions_capture.get("content") is not None:
        interactions_path = output_dir / "interactions" / "states.json"
        interactions_path.write_text(json.dumps(interactions_capture["content"], indent=2) + "\n")
        persisted["files"]["interaction_states"] = str(interactions_path)
        bundle_payload["bundle"]["captured_artifacts"]["interactions"] = {
            "available": True,
            "entry_count": interactions_capture.get("entryCount"),
            "path": str(interactions_path),
        }

    interaction_trace_capture = runtime_capture.get("interactionTrace", {}) if isinstance(runtime_capture, dict) else {}
    if interaction_trace_capture.get("available") and interaction_trace_capture.get("content") is not None:
        interaction_trace_path = output_dir / "interactions" / "trace.json"
        interaction_trace_path.write_text(json.dumps(interaction_trace_capture["content"], indent=2) + "\n")
        persisted["files"]["interaction_trace"] = str(interaction_trace_path)
        bundle_payload["bundle"]["captured_artifacts"]["interaction_trace"] = {
            "available": True,
            "step_count": interaction_trace_capture.get("stepCount"),
            "replayed_count": interaction_trace_capture.get("replayedCount"),
            "path": str(interaction_trace_path),
        }

    html_capture = runtime_capture.get("html", {}) if isinstance(runtime_capture, dict) else {}
    if html_capture.get("available") and html_capture.get("content"):
        html_path = output_dir / "dom" / "runtime.html"
        html_path.write_text(html_capture["content"])
        persisted["files"]["html"] = str(html_path)
        bundle_payload["bundle"]["captured_artifacts"]["html"] = {
            "available": True,
            "length": html_capture.get("length", 0),
            "path": str(html_path),
        }

    screenshot_capture = runtime_capture.get("screenshot", {}) if isinstance(runtime_capture, dict) else {}
    if screenshot_capture.get("available") and screenshot_capture.get("base64"):
        screenshot_path = output_dir / "screenshots" / "runtime.png"
        screenshot_path.write_bytes(base64.b64decode(screenshot_capture["base64"]))
        persisted["files"]["screenshot"] = str(screenshot_path)
        bundle_payload["bundle"]["captured_artifacts"]["screenshot"] = {
            "available": True,
            "mimeType": screenshot_capture.get("mimeType", "image/png"),
            "byteLength": screenshot_capture.get("byteLength", 0),
            "path": str(screenshot_path),
        }

    session = runtime.get("session", {}) if isinstance(runtime, dict) else {}
    storage_state_output_path = session.get("storageStateOutputPath")
    if storage_state_output_path and Path(storage_state_output_path).exists():
        target = output_dir / "session" / "storage-state.json"
        if Path(storage_state_output_path).resolve() != target.resolve():
            shutil.copy2(storage_state_output_path, target)
        persisted["files"]["storage_state"] = str(target)
        bundle_payload["bundle"]["captured_artifacts"]["session"] = {
            "storage_state_exported": True,
            "path": str(target),
            "mode": session.get("mode"),
        }

    capture_json_path = output_dir / "capture.json"
    capture_json_path.write_text(json.dumps(bundle_payload, indent=2) + "\n")
    persisted["files"]["capture_manifest"] = str(capture_json_path)

    return persisted


def _standardize_har_like(har_like: dict[str, Any]) -> dict[str, Any]:
    pages = har_like.get("pages") if isinstance(har_like.get("pages"), list) else []
    entries = har_like.get("entries") if isinstance(har_like.get("entries"), list) else []
    summary = har_like.get("summary") if isinstance(har_like.get("summary"), dict) else {}
    return {
        "log": {
            "version": "1.2",
            "creator": {
                "name": "webEmbedding",
                "version": "0.1",
            },
            "browser": {
                "name": "Playwright Chromium",
                "version": None,
            },
            "pages": pages,
            "entries": entries,
        },
        "summary": {
            "pageCount": summary.get("pageCount", len(pages)),
            "entryCount": summary.get("entryCount", len(entries)),
            "requestCount": summary.get("requestCount"),
            "responseCount": summary.get("responseCount"),
            "failureCount": summary.get("failureCount"),
            "redirectCount": summary.get("redirectCount"),
        },
    }
