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


FIXTURE_CASES = {
    "fixture://public-app-gate": {
        "title": "Public app gate fixture",
        "final_url": "https://fixture.example/product/1",
        "html": "<main><a>앱에서 보기</a><a href='intent://product/1'>Open app</a><p>로그인 후 시세를 확인하세요</p><iframe src='https://fixture.example/promo'></iframe></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [{"kind": "direct-iframe", "url": "https://fixture.example/promo"}],
        "source_signals": [],
    },
    "fixture://static-marketing": {
        "title": "Static marketing fixture",
        "final_url": "https://fixture.example/marketing",
        "html": "<main><header><h1>Launch faster</h1><a href='/pricing'>Pricing</a></header><section><p>Simple public landing page.</p></section></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://static-docs": {
        "title": "Static docs fixture",
        "final_url": "https://fixture.example/docs",
        "html": "<main><article><h1>Docs</h1><p>Install.</p><p>Configure.</p><p>Deploy.</p></article><nav><a>API</a><a>Guide</a></nav></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://longform-article": {
        "title": "Longform article fixture",
        "final_url": "https://fixture.example/article",
        "html": "<main><article><h1>Research report</h1><section><h2>Intro</h2><p>A</p><p>B</p><p>C</p></section><section><h2>Method</h2><p>D</p><p>E</p><p>F</p></section><section><h2>Findings</h2><p>G</p><p>H</p><p>I</p></section><section><h2>Appendix</h2><p>J</p><p>K</p><p>L</p></section></article></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://content-hub": {
        "title": "Content hub fixture",
        "final_url": "https://fixture.example/hub",
        "html": "<main><section><h1>Hub</h1><p>Collections</p></section><section><h2>A</h2><ul>"
        + "".join("<li><a>Item</a></li>" for _ in range(24))
        + "</ul></section><section><h2>B</h2><p>More links</p></section><section><h2>C</h2><p>More links</p></section></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://platform-notion": {
        "title": "Platform Notion fixture",
        "final_url": "https://fixture.example/notion",
        "html": "<main><div class='notion-page'>Knowledge base</div><script src='https://notion-static.com/app.js'></script></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "notion"},
        "candidate_urls": [],
        "source_signals": ["source"],
    },
    "fixture://platform-shopify": {
        "title": "Platform Shopify fixture",
        "final_url": "https://fixture.example/store",
        "html": "<main><section class='shopify-section'><h1>Product</h1><button>Add to cart</button></section><script>window.Shopify={}</script></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "shopify"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://platform-framer": {
        "title": "Platform Framer fixture",
        "final_url": "https://fixture.example/framer",
        "html": "<main><section><h1>Framer site</h1></section><script src='https://framerusercontent.com/site.js'></script></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "framer"},
        "candidate_urls": [],
        "source_signals": ["remix"],
    },
    "fixture://js-app-shell-react": {
        "title": "React app shell fixture",
        "final_url": "https://fixture.example/app",
        "html": "<main id='root'><nav>Home</nav><section>Workspace</section></main><script src='/react-dom.js'></script><script>window.__APP__=true</script>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://js-app-shell-next": {
        "title": "Next app shell fixture",
        "final_url": "https://fixture.example/next",
        "html": "<div id='__next'><aside>Projects</aside><main>Board</main></div><script src='/_next/static/app.js'></script>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://dashboard-table": {
        "title": "Dashboard table fixture",
        "final_url": "https://fixture.example/dashboard",
        "html": "<div id='root'><header>Admin</header><aside>Filters</aside><main><table><tr><th>Name</th></tr><tr><td>Alpha</td></tr></table></main></div><script src='/react-dom.js'></script>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://frame-blocked-app": {
        "title": "Frame blocked app fixture",
        "final_url": "https://fixture.example/blocked-app",
        "html": "<div id='root'><main>Blocked workspace</main></div><script src='/react-dom.js'></script>",
        "frame_policy": {"embeddable": False, "reason": "CSP frame-ancestors blocks reuse."},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://auth-app-shell": {
        "title": "Authenticated app shell fixture",
        "final_url": "https://fixture.example/account",
        "html": "<main id='root'><form><input type='password'><button>Sign in</button></form></main><script src='/react-dom.js'></script>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://auth-oauth-shell": {
        "title": "OAuth shell fixture",
        "final_url": "https://fixture.example/oauth",
        "html": "<main id='root'><h1>Account</h1><a>Login with OAuth</a><p>Okta authorization required.</p></main><script src='/_next/static/app.js'></script>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://canvas-webgl": {
        "title": "Canvas WebGL fixture",
        "final_url": "https://fixture.example/webgl",
        "html": "<main><canvas id='stage'></canvas><script src='/three.js'></script><p>webgl animation</p></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://canvas-overlay": {
        "title": "Canvas overlay fixture",
        "final_url": "https://fixture.example/canvas-overlay",
        "html": "<main><canvas></canvas><canvas></canvas><section><button>Play</button><p>Realtime stage</p></section></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://multi-frame-doc": {
        "title": "Multi frame doc fixture",
        "final_url": "https://fixture.example/frames",
        "html": "<main><h1>Frames</h1><iframe src='/a'></iframe><iframe src='/b'></iframe><p>Reference frames.</p></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://shadow-dom-widget": {
        "title": "Shadow DOM widget fixture",
        "final_url": "https://fixture.example/shadow",
        "html": "<main><custom-widget></custom-widget><script>customElements.define('custom-widget', class extends HTMLElement{connectedCallback(){this.attachShadow({mode:'open'})}})</script></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://native-app-qr-gate": {
        "title": "Native app QR gate fixture",
        "final_url": "https://fixture.example/qr",
        "html": "<main><h1>Continue in app</h1><p>Scan the QR code to download the app.</p><a href='itms-apps://fixture/app'>Open in app</a></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
    "fixture://iframe-exact-candidate": {
        "title": "Iframe exact candidate fixture",
        "final_url": "https://fixture.example/embed",
        "html": "<main><h1>Embeddable</h1><iframe src='https://player.fixture.example/view'></iframe></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [{"kind": "direct-iframe", "url": "https://player.fixture.example/view"}],
        "source_signals": [],
    },
    "fixture://video-embed": {
        "title": "Video embed fixture",
        "final_url": "https://fixture.example/video",
        "html": "<main><h1>Video</h1><iframe src='https://www.youtube.com/embed/abc'></iframe><p>Demo video.</p></main>",
        "frame_policy": {"embeddable": True},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [{"kind": "youtube-embed", "url": "https://www.youtube.com/embed/abc"}],
        "source_signals": [],
    },
    "fixture://frame-blocked-longform": {
        "title": "Frame blocked longform fixture",
        "final_url": "https://fixture.example/blocked-doc",
        "html": "<main><section><h1>Blocked report</h1><p>A</p><p>B</p><p>C</p></section><section><h2>Details</h2><p>D</p><p>E</p><p>F</p></section><section><h2>More</h2><p>G</p><p>H</p><p>I</p></section><section><h2>Links</h2><ul>"
        + "".join("<li><a>Link</a></li>" for _ in range(20))
        + "</ul></section></main>",
        "frame_policy": {"embeddable": False, "reason": "X-Frame-Options denies embedding."},
        "platform_adapter": {"platform": "generic"},
        "candidate_urls": [],
        "source_signals": [],
    },
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_api() -> Any:
    capture_root = repo_root() / "bundle" / "source-first-clone" / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.acquisition import inspect_reference
    from source_first_clone.capture_bundle import capture_reference_bundle
    from source_first_clone.failure_taxonomy import classify_pipeline_failure, network_replay_readiness
    from source_first_clone.site_profile import classify_site_profile

    return inspect_reference, capture_reference_bundle, classify_site_profile, classify_pipeline_failure, network_replay_readiness


def inspect_fixture(url: str, classify_site_profile: Any) -> dict[str, Any]:
    fixture = FIXTURE_CASES.get(url)
    if not fixture:
        raise ValueError(f"Unknown benchmark fixture URL: {url}")
    profile = classify_site_profile(
        final_url=str(fixture["final_url"]),
        html=str(fixture["html"]),
        headers={},
        frame_policy=fixture["frame_policy"],
        platform_adapter=fixture["platform_adapter"],
        candidate_urls=fixture["candidate_urls"],
    )
    return {
        "url": url,
        "final_url": fixture["final_url"],
        "status": 200,
        "title": fixture["title"],
        "platform": fixture["platform_adapter"].get("platform", "generic"),
        "frame_policy": fixture["frame_policy"],
        "source_signals": fixture["source_signals"],
        "candidate_urls": fixture["candidate_urls"],
        "site_profile": profile,
    }


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def route_visibility(profile: dict[str, Any]) -> dict[str, Any]:
    route_hints = profile.get("route_hints", {}) if isinstance(profile.get("route_hints"), dict) else {}
    signals = profile.get("signals", {}) if isinstance(profile.get("signals"), dict) else {}
    return {
        "primary_surface": profile.get("primary_surface"),
        "confidence": profile.get("confidence"),
        "acquisition_profile": route_hints.get("acquisition_profile"),
        "renderer_route": route_hints.get("renderer_route"),
        "renderer_family": route_hints.get("renderer_family"),
        "critical_depths": _string_list(route_hints.get("critical_depths")),
        "evidence_limit": route_hints.get("evidence_limit"),
        "app_gate_detected": bool(signals.get("app_gate_detected")),
        "app_gate_signals": _string_list(signals.get("app_gate_signals")),
    }


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
    inspect_reference, capture_reference_bundle, classify_site_profile, classify_pipeline_failure, network_replay_readiness = load_api()

    items: list[dict[str, Any]] = []
    surface_counter: Counter[str] = Counter()
    route_counter: Counter[str] = Counter()
    renderer_family_counter: Counter[str] = Counter()
    acquisition_counter: Counter[str] = Counter()
    policy_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    profile_warning_counter: Counter[str] = Counter()
    depth_presence_counter: Counter[str] = Counter()
    critical_depth_counter: Counter[str] = Counter()
    evidence_limit_counter: Counter[str] = Counter()
    app_gate_signal_counter: Counter[str] = Counter()
    route_quality_counter: Counter[str] = Counter()
    pipeline_status_counter: Counter[str] = Counter()
    failure_code_counter: Counter[str] = Counter()

    for index, url in enumerate(urls, start=1):
        item: dict[str, Any] = {"url": url, "index": index}
        try:
            fixture_case = url in FIXTURE_CASES
            inspect_payload = (
                inspect_fixture(url, classify_site_profile)
                if fixture_case
                else inspect_reference(url, timeout_seconds=args.timeout_seconds)
            )
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
            item["route"] = route_visibility(profile)
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
            critical_depth_counter.update(item["route"].get("critical_depths") or [])
            evidence_limit = item["route"].get("evidence_limit")
            if evidence_limit:
                evidence_limit_counter[str(evidence_limit)] += 1
            app_gate_signal_counter.update(item["route"].get("app_gate_signals") or [])
            if args.capture and fixture_case:
                item["capture"] = {
                    "skipped": True,
                    "reason": "deterministic benchmark fixture does not execute network/runtime capture",
                }
            elif args.capture:
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
                            "replay_readiness": network_replay_readiness(network_summary),
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
        failure_classification = classify_pipeline_failure({"benchmark_item": item})
        item["failure_classification"] = failure_classification
        pipeline_status_counter[str(failure_classification.get("status") or "unknown")] += 1
        failure_code_counter.update(failure_classification.get("codes") or [])
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
            "critical_depth_counts": dict(critical_depth_counter),
            "evidence_limit_counts": dict(evidence_limit_counter),
            "app_gate_signal_counts": dict(app_gate_signal_counter),
            "route_quality_counts": dict(route_quality_counter),
            "pipeline_status_counts": dict(pipeline_status_counter),
            "failure_code_counts": dict(failure_code_counter),
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
