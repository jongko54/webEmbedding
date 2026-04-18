#!/usr/bin/env python3
"""Run a universal-mode route benchmark across multiple URLs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_api() -> Any:
    capture_root = repo_root() / "bundle" / "source-first-clone" / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.acquisition import inspect_reference
    from source_first_clone.capture_bundle import capture_reference_bundle

    return inspect_reference, capture_reference_bundle


def load_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.url or [])
    if args.urls_file:
        for line in Path(args.urls_file).expanduser().resolve().read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    if not deduped:
        raise SystemExit("Provide at least one --url or --urls-file.")
    return deduped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark universal route classification across multiple URLs.")
    parser.add_argument("--url", action="append", default=[], help="URL to include. Repeat for multiple URLs.")
    parser.add_argument("--urls-file", help="Text file with one URL per line.")
    parser.add_argument("--corpus-name", help="Optional label for the benchmark corpus or URL set.")
    parser.add_argument("--out", required=True, help="Output directory for the benchmark run.")
    parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    parser.add_argument("--capture", action="store_true", help="Also persist a shallow capture bundle per URL.")
    parser.add_argument("--skip-runtime-trace", action="store_true", help="When capturing, skip deep runtime trace and keep the benchmark static-only.")
    args = parser.parse_args(argv)

    urls = load_urls(args)
    output_root = Path(args.out).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    inspect_reference, capture_reference_bundle = load_api()

    items: list[dict[str, Any]] = []
    surface_counter: Counter[str] = Counter()
    route_counter: Counter[str] = Counter()
    renderer_family_counter: Counter[str] = Counter()
    acquisition_counter: Counter[str] = Counter()
    policy_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    profile_warning_counter: Counter[str] = Counter()
    depth_presence_counter: Counter[str] = Counter()
    route_quality_counter: Counter[str] = Counter()

    for index, url in enumerate(urls, start=1):
        item: dict[str, Any] = {"url": url, "index": index}
        try:
            inspect_payload = inspect_reference(url, timeout_seconds=args.timeout_seconds)
            profile = inspect_payload.get("site_profile", {}) if isinstance(inspect_payload.get("site_profile"), dict) else {}
            route_hints = profile.get("route_hints", {}) if isinstance(profile.get("route_hints"), dict) else {}
            item["inspect"] = {
                "platform": inspect_payload.get("platform"),
                "status": inspect_payload.get("status"),
                "frame_policy": inspect_payload.get("frame_policy"),
                "source_signals": inspect_payload.get("source_signals"),
                "candidate_count": len(inspect_payload.get("candidate_urls") or []),
                "candidate_sample": (inspect_payload.get("candidate_urls") or [])[:10],
                "site_profile": profile,
            }
            item["route"] = {
                "primary_surface": profile.get("primary_surface"),
                "confidence": profile.get("confidence"),
                "acquisition_profile": route_hints.get("acquisition_profile"),
                "renderer_route": route_hints.get("renderer_route"),
                "renderer_family": route_hints.get("renderer_family"),
            }
            profile_warnings: list[str] = []
            if not profile.get("primary_surface"):
                profile_warnings.append("missing_primary_surface")
            if not route_hints.get("acquisition_profile"):
                profile_warnings.append("missing_acquisition_profile")
            if not route_hints.get("renderer_route"):
                profile_warnings.append("missing_renderer_route")
            critical_depths = route_hints.get("critical_depths")
            if not isinstance(critical_depths, list) or not critical_depths:
                profile_warnings.append("missing_critical_depths")
            if profile_warnings:
                item["profile_warnings"] = profile_warnings
                profile_warning_counter.update(profile_warnings)
            route_quality = "complete" if not profile_warnings else "needs_attention"
            item["route_quality"] = route_quality
            route_quality_counter[route_quality] += 1
            surface_counter[str(profile.get("primary_surface") or "unknown")] += 1
            route_counter[str(route_hints.get("renderer_route") or "unknown")] += 1
            renderer_family_counter[str(route_hints.get("renderer_family") or "unknown")] += 1
            acquisition_counter[str(route_hints.get("acquisition_profile") or "unknown")] += 1
            if args.capture:
                capture_dir = output_root / f"case-{index:02d}"
                capture_payload = capture_reference_bundle(
                    url=url,
                    timeout_seconds=args.timeout_seconds,
                    include_runtime_trace=not args.skip_runtime_trace,
                    output_dir=str(capture_dir),
                )
                runtime_captures = capture_payload.get("runtime", {}).get("captures", {}) if isinstance(capture_payload.get("runtime"), dict) else {}
                html_capture = runtime_captures.get("html", {}) if isinstance(runtime_captures.get("html"), dict) else {}
                accessibility_capture = runtime_captures.get("accessibility", {}) if isinstance(runtime_captures.get("accessibility"), dict) else {}
                dom_capture = runtime_captures.get("dom", {}) if isinstance(runtime_captures.get("dom"), dict) else {}
                css_capture = runtime_captures.get("cssAnalysis", {}) if isinstance(runtime_captures.get("cssAnalysis"), dict) else {}
                assets_capture = runtime_captures.get("assets", {}) if isinstance(runtime_captures.get("assets"), dict) else {}
                interactions_capture = runtime_captures.get("interactions", {}) if isinstance(runtime_captures.get("interactions"), dict) else {}
                interaction_trace_capture = runtime_captures.get("interactionTrace", {}) if isinstance(runtime_captures.get("interactionTrace"), dict) else {}
                screenshot_capture = runtime_captures.get("screenshot", {}) if isinstance(runtime_captures.get("screenshot"), dict) else {}
                network_capture = runtime_captures.get("network", {}) if isinstance(runtime_captures.get("network"), dict) else {}
                network_summary = network_capture.get("content", {}).get("summary", {}) if isinstance(network_capture.get("content"), dict) else {}
                if html_capture.get("available"):
                    depth_presence_counter["html"] += 1
                if accessibility_capture.get("available"):
                    depth_presence_counter["accessibility"] += 1
                if dom_capture.get("shadowRootCount"):
                    depth_presence_counter["shadow_dom"] += 1
                if dom_capture.get("frameDocumentCount"):
                    depth_presence_counter["frame_documents"] += 1
                if interactions_capture.get("entryCount"):
                    depth_presence_counter["interactions"] += 1
                if interaction_trace_capture.get("stepCount"):
                    depth_presence_counter["interaction_trace"] += 1
                if screenshot_capture.get("available"):
                    depth_presence_counter["screenshot"] += 1
                if network_summary.get("requestCount"):
                    depth_presence_counter["network"] += 1
                if network_summary.get("frameUrlCount"):
                    depth_presence_counter["frame_network"] += 1
                if network_summary.get("redirectCount"):
                    depth_presence_counter["network_redirects"] += 1
                if network_summary.get("timingBucketCounts"):
                    depth_presence_counter["network_timing"] += 1
                if network_summary.get("requestHeaderPresenceSummary") or network_summary.get("responseHeaderPresenceSummary"):
                    depth_presence_counter["network_headers"] += 1
                item["capture"] = {
                    "policy_mode": capture_payload.get("policy", {}).get("mode"),
                    "reason": capture_payload.get("policy", {}).get("reason"),
                    "capture_path": str(capture_dir / "capture.json"),
                    "depth_summary": {
                        "html": {
                            "available": html_capture.get("available"),
                            "length": html_capture.get("length"),
                        },
                        "accessibility": {
                            "available": accessibility_capture.get("available"),
                        },
                        "dom": {
                            "node_count": dom_capture.get("nodeCount"),
                            "shadow_root_count": dom_capture.get("shadowRootCount"),
                            "frame_document_count": dom_capture.get("frameDocumentCount"),
                            "inaccessible_frame_count": dom_capture.get("inaccessibleFrameCount"),
                        },
                        "css": {
                            "linked_stylesheet_count": css_capture.get("linkedStylesheetCount"),
                            "preload_link_count": css_capture.get("preloadLinkCount"),
                            "font_face_rule_count": css_capture.get("fontFaceRuleCount"),
                        },
                        "network": {
                            "request_count": network_summary.get("requestCount"),
                            "response_count": network_summary.get("responseCount"),
                            "failure_count": network_summary.get("failureCount"),
                            "redirect_count": network_summary.get("redirectCount"),
                            "navigation_request_count": network_summary.get("navigationRequestCount"),
                            "post_data_request_count": network_summary.get("postDataRequestCount"),
                            "service_worker_response_count": network_summary.get("serviceWorkerResponseCount"),
                            "frame_url_count": network_summary.get("frameUrlCount"),
                            "timing_bucket_counts": network_summary.get("timingBucketCounts"),
                            "request_header_presence_summary": network_summary.get("requestHeaderPresenceSummary"),
                            "response_header_presence_summary": network_summary.get("responseHeaderPresenceSummary"),
                            "response_body_availability": network_summary.get("responseBodyAvailability"),
                            "query_parameter_count": network_summary.get("queryParameterCount"),
                            "request_cookie_count": network_summary.get("requestCookieCount"),
                            "response_cookie_count": network_summary.get("responseCookieCount"),
                            "request_header_bytes": network_summary.get("requestHeaderBytes"),
                            "response_header_bytes": network_summary.get("responseHeaderBytes"),
                            "request_body_bytes": network_summary.get("requestBodyBytes"),
                            "response_body_bytes": network_summary.get("responseBodyBytes"),
                            "response_redirect_count": network_summary.get("responseRedirectCount"),
                            "page_timings": network_summary.get("pageTimings"),
                            "har_export_path": network_summary.get("harExportPath"),
                            "har_page_count": network_summary.get("harPageCount"),
                            "har_entry_count": network_summary.get("harEntryCount"),
                            "har_like_page_count": network_summary.get("harLikePageCount"),
                            "har_like_entry_count": network_summary.get("harLikeEntryCount"),
                        },
                        "interactions": {
                            "available": interactions_capture.get("available"),
                            "entry_count": interactions_capture.get("entryCount"),
                        },
                        "interaction_trace": {
                            "available": interaction_trace_capture.get("available"),
                            "step_count": interaction_trace_capture.get("stepCount"),
                            "replayed_count": interaction_trace_capture.get("replayedCount"),
                        },
                        "screenshot": {
                            "available": screenshot_capture.get("available"),
                            "byte_length": screenshot_capture.get("byteLength"),
                            "mime_type": screenshot_capture.get("mimeType"),
                        },
                        "asset_summary": assets_capture.get("summary"),
                    },
                }
                policy_counter[str(capture_payload.get("policy", {}).get("mode") or "unknown")] += 1
            item["status"] = "ok"
            status_counter["ok"] += 1
        except Exception as exc:  # pragma: no cover - benchmark harness
            item["status"] = "error"
            item["error"] = str(exc)
            status_counter["error"] += 1
        items.append(item)

    report = {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "webEmbedding universal route benchmark",
            "timeout_seconds": args.timeout_seconds,
            "capture": bool(args.capture),
        },
        "corpus": {
            "name": args.corpus_name or (Path(args.urls_file).stem if args.urls_file else None),
            "source_file": str(Path(args.urls_file).expanduser().resolve()) if args.urls_file else None,
            "url_count": len(urls),
        },
        "inputs": urls,
        "summary": {
            "total": len(urls),
            "status_counts": dict(status_counter),
            "surface_counts": dict(surface_counter),
            "renderer_route_counts": dict(route_counter),
            "renderer_family_counts": dict(renderer_family_counter),
            "acquisition_profile_counts": dict(acquisition_counter),
            "policy_mode_counts": dict(policy_counter),
            "profile_warning_counts": dict(profile_warning_counter),
            "depth_presence_counts": dict(depth_presence_counter),
            "route_quality_counts": dict(route_quality_counter),
        },
        "items": items,
    }

    report_path = output_root / "universal-route-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(str(report_path))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
