"""Reproduction bundle generation for source-first clone workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .planning import plan_reproduction_path
from .repair_scaffold import build_repair_scaffold
from .rebuild_scaffold import build_rebuild_scaffold, persist_rebuild_scaffold
from .self_verify import (
    _breakpoint_ready_count,
    _self_verify_rank,
    _self_verify_summary,
    run_rebuild_self_verify,
)


ANALYTICS_HOST_HINTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "facebook.net",
    "connect.facebook.net",
    "px.ads.linkedin.com",
    "googleadservices.com",
    "analytics.google.com",
    "google.com/ccm/collect",
    "google.com/rmkt/collect",
    "google.co.kr/pagead",
    "google.com/pagead",
    "_vercel/speed-insights",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

MAX_AUTO_REPAIR_PASSES = 3
MIN_AUTO_REPAIR_SCORE_DELTA = 3


def is_noise_url(url: str) -> bool:
    lowered = url.lower()
    if any(hint in lowered for hint in ANALYTICS_HOST_HINTS):
        return True
    if any(
        marker in lowered
        for marker in (
            "/_next/image?url=",
            "/signals/config/",
            "/collect?",
            "/attribution_trigger",
            "/oauth/verify",
        )
    ):
        return True
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".woff", ".woff2", ".css", ".js", ".map", ".wasm")):
        return True
    return False


def infer_platform(url: str) -> str:
    lowered = url.lower()
    if "spline.design" in lowered:
        return "spline"
    if "figma.com" in lowered:
        return "figma"
    if "readymag" in lowered or "rmcdn" in lowered:
        return "readymag"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "vimeo.com" in lowered:
        return "vimeo"
    if "codepen.io" in lowered:
        return "codepen"
    if "webflow" in lowered:
        return "webflow"
    if "framer" in lowered:
        return "framer"
    return "generic"


def classify_candidate(url: str) -> str:
    lowered = url.lower()
    if "https://www.figma.com/embed" in lowered:
        return "figma-embed"
    if "https://embed.readymag.com" in lowered:
        return "readymag-embed"
    if "htmlsnippet-" in lowered and lowered.endswith(".html"):
        return "readymag-html-snippet"
    if "https://www.youtube.com/embed/" in lowered:
        return "youtube-embed"
    if "https://player.vimeo.com/video/" in lowered:
        return "vimeo-embed"
    if "https://codepen.io/" in lowered and "/embed/" in lowered:
        return "codepen-embed"
    if "app.spline.design/file/" in lowered and "view=preview" in lowered:
        return "spline-preview"
    if "viewer.spline.design/" in lowered:
        return "spline-viewer"
    if ".splinecode" in lowered:
        return "spline-code"
    if any(token in lowered for token in ("/embed", "?embed", "&embed", "view=preview", "viewer")):
        return "generic-embed"
    return "runtime-hint"


def should_reuse_direct_iframe(final_url: str, platform: str | None) -> bool:
    lowered = (final_url or "").lower()
    platform = str(platform or "").lower()
    if platform == "spline" and ("app.spline.design/file/" in lowered or "/community/file/" in lowered):
        return False
    if platform == "figma" and "figma.com/embed" not in lowered:
        return False
    return True


def collect_reuse_candidates(capture_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(url: str, source: str, kind: str | None = None) -> None:
        if not url or url in seen or is_noise_url(url):
            return
        seen.add(url)
        candidate_kind = kind or classify_candidate(url)
        candidates.append(
            {
                "url": url,
                "source": source,
                "kind": candidate_kind,
                "platform": infer_platform(url),
            }
        )

    static = capture_bundle.get("static", {})
    frame_policy = static.get("frame_policy", {}) if isinstance(static, dict) else {}
    if frame_policy.get("embeddable") is True and static.get("final_url") and should_reuse_direct_iframe(static.get("final_url"), static.get("platform")):
        add(static["final_url"], "static.frame_policy", "direct-iframe")
    for item in static.get("candidate_urls", []) or []:
        if isinstance(item, dict):
            add(item.get("url"), "static", item.get("kind"))

    runtime = capture_bundle.get("runtime", {})
    for item in runtime.get("networkHits", []) or []:
        if isinstance(item, dict):
            add(item.get("url"), "runtime.networkHits")
    for url in runtime.get("htmlMatches", []) or []:
        if isinstance(url, str):
            add(url, "runtime.htmlMatches")

    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    network_capture = captures.get("network", {}) if isinstance(captures, dict) else {}
    network_content = network_capture.get("content", {}) if isinstance(network_capture, dict) else {}
    for item in network_content.get("requests", []) or []:
        if isinstance(item, dict):
            add(item.get("url"), "runtime.network.requests")
    for item in network_content.get("responses", []) or []:
        if isinstance(item, dict):
            add(item.get("url"), "runtime.network.responses")

    assets_capture = captures.get("assets", {}) if isinstance(captures, dict) else {}
    assets_content = assets_capture.get("content", {}) if isinstance(assets_capture, dict) else {}
    for url in assets_content.get("iframes", []) or []:
        if isinstance(url, str):
            add(url, "runtime.assets.iframes", "iframe-src")

    return candidates


def choose_exact_reuse_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    priority = {
        "direct-iframe": 0,
        "spline-preview": 1,
        "spline-viewer": 2,
        "figma-embed": 3,
        "youtube-embed": 4,
        "vimeo-embed": 5,
        "codepen-embed": 6,
        "readymag-embed": 7,
        "generic-embed": 8,
        "iframe-src": 9,
        "spline-code": 10,
        "readymag-html-snippet": 11,
        "runtime-hint": 12,
    }
    exact_candidates = sorted(
        candidates,
        key=lambda item: (priority.get(item.get("kind", "runtime-hint"), 99), item.get("url", "")),
    )
    for candidate in exact_candidates:
        if candidate["kind"] in {
            "direct-iframe",
            "spline-preview",
            "spline-viewer",
            "figma-embed",
            "youtube-embed",
            "vimeo-embed",
            "codepen-embed",
            "readymag-embed",
            "generic-embed",
            "iframe-src",
        }:
            return candidate
    return None


def build_embed_snippets(url: str, title: str) -> dict[str, str]:
    html = (
        f'<iframe src="{url}" title="{title}" '
        'style="display:block;width:100%;height:100vh;border:0" allow="fullscreen"></iframe>'
    )
    nextjs = "\n".join(
        [
            "<iframe",
            f'  src="{url}"',
            f'  title="{title}"',
            '  allow="fullscreen"',
            '  style={{ display: "block", width: "100%", height: "100vh", border: 0 }}',
            "/>",
        ]
    )
    return {"html": html, "nextjs": nextjs}


def _collect_dom_texts(node: dict[str, Any] | None, bucket: list[str], limit: int = 12) -> None:
    if not isinstance(node, dict) or len(bucket) >= limit:
        return
    text = str(node.get("text") or "").strip()
    if text and text not in bucket:
        bucket.append(text)
    for child in node.get("children", []) or []:
        _collect_dom_texts(child, bucket, limit=limit)
        if len(bucket) >= limit:
            break


def build_rebuild_prompt(capture_bundle: dict[str, Any]) -> str:
    static = capture_bundle.get("static", {})
    meta = static.get("meta", {}) if isinstance(static, dict) else {}
    runtime = capture_bundle.get("runtime", {})
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    dom_capture = captures.get("dom", {}) if isinstance(captures, dict) else {}
    styles_capture = captures.get("styles", {}) if isinstance(captures, dict) else {}
    css_analysis_capture = captures.get("cssAnalysis", {}) if isinstance(captures, dict) else {}
    assets_capture = captures.get("assets", {}) if isinstance(captures, dict) else {}
    interactions_capture = captures.get("interactions", {}) if isinstance(captures, dict) else {}
    interaction_trace_capture = captures.get("interactionTrace", {}) if isinstance(captures, dict) else {}
    breakpoint_summary = capture_bundle.get("breakpoints", {}) if isinstance(capture_bundle, dict) else {}

    prompt_lines = [
        "Rebuild this reference as faithfully as possible using the captured structure and styling summary.",
        f"Reference URL: {capture_bundle.get('url')}",
        f"Final URL: {static.get('final_url')}",
    ]
    platform = static.get("platform") or "generic"
    platform_adapter = static.get("platform_adapter", {}) if isinstance(static, dict) else {}
    source_signals = static.get("source_signals", []) if isinstance(static, dict) else []
    candidate_urls = static.get("candidate_urls", []) if isinstance(static, dict) else []
    site_profile = static.get("site_profile", {}) if isinstance(static, dict) else {}
    if platform != "generic":
        prompt_lines.append(f"Platform: {platform}")
    if isinstance(platform_adapter, dict):
        adapter_notes = platform_adapter.get("notes", []) if isinstance(platform_adapter.get("notes", []), list) else []
        if adapter_notes:
            prompt_lines.append("Platform adapter notes:")
            prompt_lines.extend(f"- {str(note)}" for note in adapter_notes[:4])
    if source_signals:
        prompt_lines.append("Source signals:")
        prompt_lines.extend(f"- {signal}" for signal in source_signals[:6])
    if candidate_urls:
        prompt_lines.append("Reuse candidates:")
        for item in candidate_urls[:6]:
            if not isinstance(item, dict):
                continue
            prompt_lines.append(f"- {item.get('kind')}: {item.get('url')}")
    if isinstance(site_profile, dict) and site_profile:
        route_hints = site_profile.get("route_hints", {}) if isinstance(site_profile.get("route_hints"), dict) else {}
        prompt_lines.append(f"Site profile: {site_profile.get('primary_surface')} ({site_profile.get('confidence')})")
        if route_hints:
            prompt_lines.append(
                f"Route hints: acquisition={route_hints.get('acquisition_profile')} renderer={route_hints.get('renderer_route')} family={route_hints.get('renderer_family')}"
            )
            critical_depths = route_hints.get("critical_depths", [])
            if critical_depths:
                prompt_lines.append("Critical capture depths:")
                prompt_lines.extend(f"- {depth}" for depth in critical_depths[:8])
            if str(site_profile.get("primary_surface") or "").lower() in {"js-app-shell-surface", "authenticated-app-surface", "frame-blocked-app-surface"}:
                prompt_lines.append("App-shell guidance:")
                prompt_lines.extend(
                    [
                        "- Preserve shell chrome, toolbar rows, navigation rails, workspace panels, and inspector-like regions.",
                        "- Keep panel and state boundaries explicit; do not collapse the surface into a centered landing-page layout.",
                        "- Prefer dashboard-style composition with stable sidebars and content panes before hero-style compression.",
                        "- Keep shell topology legible in the rebuild prompt: navigation, workspace, inspector, and auxiliary regions should remain separable.",
                        "- Preserve region order and primary panel emphasis when available; do not flatten the shell into one generic content column.",
                    ]
                )
            if str(site_profile.get("primary_surface") or "").lower() == "canvas-or-webgl-surface" or str(route_hints.get("renderer_route") or "").lower() == "visual-fallback-rebuild":
                prompt_lines.append("Visual fallback scaffold:")
                prompt_lines.extend(
                    [
                        "- Treat this as a visual-first rebuild, not a DOM-perfect source clone.",
                        "- Preserve stage geometry, dominant palette, hierarchy, and stable controls before trying to mirror implementation details.",
                        "- Prefer screenshot-led composition checks, then layer DOM and CSS fidelity on top.",
                    ]
                )
                prompt_lines.append("Visual fallback rendering constraints:")
                prompt_lines.extend(
                    [
                        "- Treat the canvas/WebGL region as the primary stage, not as an inspectable DOM subtree.",
                        "- Recreate overlay chrome, captions, and controls as bounded HTML/CSS around the stage.",
                        "- Use viewport screenshots and runtime HTML as the anchor for composition fidelity.",
                        "- Keep responsive behavior aligned to the captured viewport geometry.",
                    ]
                )
                prompt_lines.append("Visual fallback scaffold hints:")
                prompt_lines.extend(
                    [
                        "- single-stage layout with overlay chrome",
                        "- bounded caption and control rails",
                        "- viewport-anchored stage geometry",
                        "- screenshot-led composition checks",
                        "- DOM and CSS fidelity layered after the visual pass",
                    ]
                )
                prompt_lines.append("Visual fallback verification hints:")
                prompt_lines.extend(
                    [
                        "- confirm stage bounds align to the captured viewport",
                        "- keep overlay controls visible and reachable",
                        "- preserve palette and contrast from the reference",
                        "- check responsive variants against the same composition hierarchy",
                        "- use screenshot similarity before judging DOM-level detail",
                        "- keep stage, chrome, and caption layers separable in the rebuild",
                    ]
                )
                prompt_lines.append("Visual fallback capture hints:")
                prompt_lines.extend(
                    [
                        "- capture canvas or WebGL element bounds and viewport screenshots",
                        "- record overlay controls, caption text, and fixed-position affordances",
                        "- keep breakpoint-specific composition notes for desktop, tablet, and mobile",
                        "- include asset inventory and interaction trace samples for hover, click, and scroll states",
                    ]
                )

    if static.get("title"):
        prompt_lines.append(f"Page title: {static['title']}")
    if meta.get("description"):
        prompt_lines.append(f"Description: {meta['description']}")
    if isinstance(breakpoint_summary, dict) and breakpoint_summary.get("variants"):
        prompt_lines.append("Breakpoint coverage:")
        primary = breakpoint_summary.get("primary", {})
        if isinstance(primary, dict):
            prompt_lines.append(f"- primary {primary.get('width')}x{primary.get('height')}")
        for variant in breakpoint_summary.get("variants", [])[:6]:
            if not isinstance(variant, dict):
                continue
            viewport = variant.get("viewport", {}) if isinstance(variant.get("viewport"), dict) else {}
            prompt_lines.append(
                f"- {variant.get('name')} {viewport.get('width')}x{viewport.get('height')} available={variant.get('available')}"
            )

    dom_texts: list[str] = []
    if dom_capture.get("available"):
        _collect_dom_texts(dom_capture.get("content"), dom_texts)
    if dom_texts:
        prompt_lines.append("Visible text sample:")
        prompt_lines.extend(f"- {text}" for text in dom_texts[:10])

    style_entries = styles_capture.get("content", []) if isinstance(styles_capture, dict) else []
    if style_entries:
        prompt_lines.append("Representative visual blocks:")
        for entry in style_entries[:8]:
            rect = entry.get("rect", {})
            styles = entry.get("styles", {})
            descriptor = " ".join(
                value
                for value in [
                    entry.get("tag"),
                    f"\"{entry.get('text')}\"" if entry.get("text") else "",
                    f"{rect.get('width')}x{rect.get('height')}" if rect.get("width") else "",
                    styles.get("fontFamily"),
                    styles.get("fontSize"),
                    styles.get("fontWeight"),
                    styles.get("color"),
                    styles.get("backgroundColor"),
                ]
                if value
            ).strip()
            if descriptor:
                prompt_lines.append(f"- {descriptor}")

    css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
    linked_stylesheets = css_analysis.get("linkedStylesheets", []) if isinstance(css_analysis.get("linkedStylesheets", []), list) else []
    inline_style_blocks = css_analysis.get("inlineStyleBlocks", []) if isinstance(css_analysis.get("inlineStyleBlocks", []), list) else []
    if linked_stylesheets or inline_style_blocks:
        prompt_lines.append("CSS analysis sample:")
        if isinstance(css_analysis, dict):
            prompt_lines.append(
                f"- stylesheets: {css_analysis.get('stylesheetCount', 0)} total / {css_analysis.get('accessibleStylesheetCount', 0)} accessible"
            )
            prompt_lines.append(
                f"- inline style blocks: {css_analysis.get('inlineStyleTagCount', 0)} / style attributes: {css_analysis.get('styleAttributeCount', 0)}"
            )
        for sheet in linked_stylesheets[:4]:
            if not isinstance(sheet, dict):
                continue
            descriptor = " ".join(
                value
                for value in [
                    sheet.get("ownerTag"),
                    sheet.get("href") or "inline-sheet",
                    f"rules={sheet.get('ruleCount')}" if sheet.get("ruleCount") is not None else "",
                    "restricted" if sheet.get("crossOriginRestricted") else "",
                ]
                if value
            ).strip()
            if descriptor:
                prompt_lines.append(f"- {descriptor}")
        for block in inline_style_blocks[:2]:
            if not isinstance(block, dict):
                continue
            sample = str(block.get("textSample") or "").strip()
            if sample:
                prompt_lines.append(f"- inline style sample: {sample}")

    asset_content = assets_capture.get("content", {}) if isinstance(assets_capture, dict) else {}
    iframe_count = len(asset_content.get("iframes", []) or []) if isinstance(asset_content, dict) else 0
    image_count = len(asset_content.get("images", []) or []) if isinstance(asset_content, dict) else 0
    script_count = len(asset_content.get("scripts", []) or []) if isinstance(asset_content, dict) else 0
    if image_count or script_count or iframe_count:
        prompt_lines.append(
            f"Asset signals: {image_count} images, {script_count} scripts, {iframe_count} iframes detected."
        )

    interaction_entries = interactions_capture.get("content", []) if isinstance(interactions_capture, dict) else []
    if interaction_entries:
        prompt_lines.append("Interaction state sample:")
        for entry in interaction_entries[:8]:
            changed_states = []
            if entry.get("hoverDelta"):
                changed_states.append(f"hover {', '.join(entry['hoverDelta'].keys())}")
            if entry.get("focusDelta"):
                changed_states.append(f"focus {', '.join(entry['focusDelta'].keys())}")
            click_state = entry.get("clickState") or {}
            click_delta = click_state.get("stateDelta") if isinstance(click_state, dict) else {}
            if isinstance(click_delta, dict) and click_delta:
                changed_states.append(f"click {', '.join(click_delta.keys())}")
            descriptor = " ".join(
                value
                for value in [
                    entry.get("tag"),
                    entry.get("role") or "",
                    f"\"{entry.get('text')}\"" if entry.get("text") else "",
                    entry.get("href") or "",
                ]
                if value
            ).strip()
            if changed_states:
                prompt_lines.append(f"- {descriptor}: {'; '.join(changed_states)}")
            elif descriptor:
                prompt_lines.append(f"- {descriptor}: interactive element detected")

    interaction_trace = interaction_trace_capture.get("content", {}) if isinstance(interaction_trace_capture, dict) else {}
    trace_steps = interaction_trace.get("steps", []) if isinstance(interaction_trace, dict) else []
    if trace_steps:
        prompt_lines.append("Replay trace sample:")
        for step in trace_steps[:10]:
            if not isinstance(step, dict):
                continue
            parts = [
                str(step.get("kind") or "").strip(),
                str(step.get("label") or "").strip(),
                f"scrollY={step.get('scrollY')}" if step.get("kind") == "scroll" and step.get("scrollY") is not None else "",
                f"value=\"{step.get('value')}\"" if step.get("kind") == "type" and step.get("value") else "",
            ]
            descriptor = " ".join(part for part in parts if part)
            if descriptor:
                prompt_lines.append(f"- {descriptor}")

    prompt_lines.extend(
        [
            "If an exact iframe, preview, or source reuse path is unavailable, rebuild the page but do not claim it is exact.",
            "Preserve the same hierarchy, copy rhythm, dominant blocks, viewport composition, interaction state changes, and replay trace intent from the capture bundle.",
        ]
    )
    return "\n".join(prompt_lines)


def _self_verify_score(self_verify: dict[str, Any] | None) -> int:
    if not isinstance(self_verify, dict):
        return 0
    return int(_self_verify_summary(self_verify).get("score") or 0)


def _repair_pass_summary(
    pass_index: int,
    current_score: int,
    current_rank: tuple[int, int, int, int, int, int],
    repair_verify: dict[str, Any],
    next_score: int,
    next_rank: tuple[int, int, int, int, int, int],
) -> dict[str, Any]:
    preferred = repair_verify.get("preferred_renderer") or {}
    summary = _self_verify_summary(repair_verify)
    return {
        "index": pass_index,
        "source_score": current_score,
        "score": next_score,
        "score_delta": next_score - current_score,
        "improved": next_score > current_score or next_rank > current_rank,
        "meets_minimum_delta": (next_score - current_score) >= MIN_AUTO_REPAIR_SCORE_DELTA,
        "overall_ready_for_exact_clone": bool(repair_verify.get("overall_ready_for_exact_clone")),
        "stage_path": None,
        "preferred_renderer": {
            "name": preferred.get("name"),
            "kind": preferred.get("kind"),
            "score": preferred.get("score"),
            "ready_for_exact_clone": preferred.get("ready_for_exact_clone"),
            "report_path": preferred.get("report_path"),
        },
        "renderer_count": repair_verify.get("renderer_count"),
        "breakpoint_count": summary.get("breakpoint_count"),
        "breakpoint_ready_count": summary.get("breakpoint_ready_count"),
        "rank": list(next_rank),
    }


def _build_repair_loop(
    capture_bundle: dict[str, Any],
    rebuild_artifacts: dict[str, str],
    initial_self_verify: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    if not isinstance(rebuild_artifacts, dict) or not isinstance(initial_self_verify, dict):
        return {
            "available": False,
            "status": "skipped",
            "reason": "Repair loop requires a persisted rebuild scaffold and initial self-verify result.",
        }

    if initial_self_verify.get("overall_ready_for_exact_clone"):
        return {
            "available": False,
            "status": "skipped",
            "reason": "Initial bounded renderer already met the exact-clone readiness threshold.",
        }

    current_rebuild_artifacts = rebuild_artifacts
    current_self_verify = initial_self_verify
    current_score = _self_verify_score(initial_self_verify)
    current_rank = _self_verify_rank(initial_self_verify)
    best_score = current_score
    best_rank = current_rank
    best_pass: dict[str, Any] | None = None
    attempted_passes: list[dict[str, Any]] = []
    persisted_passes: list[dict[str, Any]] = []
    stop_reason = "max-passes-reached"

    for pass_index in range(1, MAX_AUTO_REPAIR_PASSES + 1):
        repair_pass = build_repair_scaffold(
            capture_bundle=capture_bundle,
            rebuild_artifacts=current_rebuild_artifacts,
            self_verify=current_self_verify,
        )
        if not repair_pass.get("available"):
            stop_reason = str(repair_pass.get("reason") or "repair-scaffold-unavailable")
            break

        stage_path = Path("repair-loop") / f"pass-{pass_index}"
        persisted_repair = persist_rebuild_scaffold(output_root / "reproduction" / stage_path, repair_pass)
        preferred_renderer = _preferred_renderer_hint(persisted_repair)
        if preferred_renderer:
            persisted_repair["preferred_renderer"] = preferred_renderer
        repair_verify = run_rebuild_self_verify(
            reference_bundle=capture_bundle,
            rebuild_artifacts=persisted_repair,
            output_dir=output_root,
            stage_path=str(stage_path / "self-verify"),
        )
        next_score = _self_verify_score(repair_verify)
        next_rank = _self_verify_rank(repair_verify)
        score_delta = next_score - current_score
        pass_summary = _repair_pass_summary(
            pass_index=pass_index,
            current_score=current_score,
            current_rank=current_rank,
            repair_verify=repair_verify,
            next_score=next_score,
            next_rank=next_rank,
        )
        pass_summary["stage_path"] = str(stage_path)
        repair_pass["persisted"] = persisted_repair
        repair_pass["self_verify"] = repair_verify
        repair_pass["iteration"] = pass_summary
        repair_summary = _self_verify_summary(repair_verify)
        repair_pass["renderer_summary"] = repair_summary.get("preferred_renderer")
        repair_pass["self_verify_summary"] = repair_summary
        attempted_passes.append(repair_pass)
        persisted_passes.append(
            {
                "index": pass_index,
                "artifacts": persisted_repair,
                "self_verify": repair_verify.get("persisted"),
                "self_verify_summary": repair_pass["self_verify_summary"],
                "renderer_summary": repair_pass["renderer_summary"],
            }
        )

        if next_rank > best_rank or next_score > best_score:
            best_score = next_score
            best_rank = next_rank
            best_pass = repair_pass

        if repair_verify.get("overall_ready_for_exact_clone"):
            best_pass = repair_pass
            stop_reason = "ready-for-exact-clone"
            break
        next_ready_breakpoints = _breakpoint_ready_count(repair_verify)
        current_ready_breakpoints = _breakpoint_ready_count(current_self_verify)
        made_progress = (
            score_delta >= MIN_AUTO_REPAIR_SCORE_DELTA
            or next_rank > current_rank
            or next_ready_breakpoints > current_ready_breakpoints
        )
        if not made_progress:
            stop_reason = "no-improvement" if score_delta <= 0 else "insufficient-score-delta"
            break
        if score_delta < MIN_AUTO_REPAIR_SCORE_DELTA and next_ready_breakpoints <= current_ready_breakpoints and next_rank <= current_rank:
            stop_reason = "insufficient-score-delta"
            break

        current_rebuild_artifacts = persisted_repair
        current_self_verify = repair_verify
        current_score = next_score
        current_rank = next_rank
    else:
        stop_reason = "max-passes-reached"

    if best_pass is None and attempted_passes:
        best_pass = attempted_passes[0]
        best_score = _self_verify_score(best_pass.get("self_verify"))
        best_rank = _self_verify_rank(best_pass.get("self_verify"))

    if best_pass is None:
        return {
            "available": False,
            "status": "skipped",
            "reason": "Repair loop did not produce a usable pass.",
        }

    best_iteration = best_pass.get("iteration", {}) if isinstance(best_pass.get("iteration", {}), dict) else {}
    return {
        "available": True,
        "status": "completed",
        "pass_count": len(attempted_passes),
        "max_passes": MAX_AUTO_REPAIR_PASSES,
        "minimum_score_delta": MIN_AUTO_REPAIR_SCORE_DELTA,
        "initial_score": _self_verify_score(initial_self_verify),
        "best_score": best_score,
        "best_rank": list(best_rank),
        "best_pass_index": best_iteration.get("index"),
        "overall_ready_for_exact_clone": bool((best_pass.get("self_verify") or {}).get("overall_ready_for_exact_clone")),
        "stop_reason": stop_reason,
        "passes": attempted_passes,
        "best_pass": best_pass,
        "persisted": {"passes": persisted_passes},
        "note": "This bounded repair loop repeats scaffold repair only while verification score keeps improving by a meaningful margin.",
    }


def build_reproduction_bundle(
    capture_bundle: dict[str, Any],
    output_dir: str | None = None,
) -> dict[str, Any]:
    static = capture_bundle.get("static", {})
    policy = capture_bundle.get("policy", {})
    candidates = collect_reuse_candidates(capture_bundle)
    exact_candidate = choose_exact_reuse_candidate(candidates)
    title = static.get("title") or "Embedded reference"
    plan = plan_reproduction_path(
        candidates=static.get("candidate_urls"),
        site_profile=static.get("site_profile"),
        capture_bundle=capture_bundle,
    )

    result: dict[str, Any] = {
        "policy_mode": policy.get("mode"),
        "plan": plan,
        "candidate_count": len(candidates),
        "candidates": candidates[:40],
        "site_profile": static.get("site_profile"),
        "visual_fallback": plan.get("visual_fallback"),
        "exact_reuse": None,
        "coverage": "approximate",
        "next_action": "rebuild",
        "note": "This reproduction bundle prefers exact reuse when a trusted preview, viewer, or embed URL is found. When that path is unavailable it falls back to a bounded rebuild scaffold plus a practical Next app starter.",
        "rebuild_prompt": build_rebuild_prompt(capture_bundle),
    }

    if exact_candidate:
        snippets = build_embed_snippets(exact_candidate["url"], title)
        result["exact_reuse"] = {
            "platform": exact_candidate["platform"],
            "kind": exact_candidate["kind"],
            "source": exact_candidate["source"],
            "url": exact_candidate["url"],
            "snippets": snippets,
        }
        result["coverage"] = "exact-reuse"
        result["next_action"] = "embed"
    else:
        result["rebuild_scaffold"] = build_rebuild_scaffold(capture_bundle)
        if any(candidate["kind"] == "spline-code" for candidate in candidates):
            result["coverage"] = "source-reuse"
            result["next_action"] = "source"
        else:
            result["next_action"] = "rebuild"

    if output_dir:
        output_root = Path(output_dir).expanduser().resolve()
        persisted = persist_reproduction_bundle(output_root, result)
        result["persisted"] = persisted
        rebuild_artifacts = persisted.get("rebuild_scaffold")
        if isinstance(rebuild_artifacts, dict):
            preferred_renderer = _preferred_renderer_hint(rebuild_artifacts)
            if preferred_renderer:
                rebuild_artifacts["preferred_renderer"] = preferred_renderer
            self_verify = run_rebuild_self_verify(
                reference_bundle=capture_bundle,
                rebuild_artifacts=rebuild_artifacts,
                output_dir=output_root,
            )
            result["self_verify"] = self_verify
            persisted["self_verify"] = self_verify.get("persisted")
            if not self_verify.get("overall_ready_for_exact_clone"):
                repair_loop = _build_repair_loop(
                    capture_bundle=capture_bundle,
                    rebuild_artifacts=rebuild_artifacts,
                    initial_self_verify=self_verify,
                    output_root=output_root,
                )
                if repair_loop.get("available"):
                    best_pass = repair_loop.get("best_pass")
                    if isinstance(best_pass, dict):
                        result["repair_pass"] = best_pass
                        persisted["repair_pass"] = {
                            "artifacts": best_pass.get("persisted"),
                            "self_verify": ((best_pass.get("self_verify") or {}).get("persisted")),
                        }
                    result["repair_passes"] = repair_loop.get("passes")
                    result["repair_loop"] = {
                        "status": repair_loop.get("status"),
                        "pass_count": repair_loop.get("pass_count"),
                        "max_passes": repair_loop.get("max_passes"),
                        "minimum_score_delta": repair_loop.get("minimum_score_delta"),
                        "initial_score": repair_loop.get("initial_score"),
                        "best_score": repair_loop.get("best_score"),
                        "best_pass_index": repair_loop.get("best_pass_index"),
                        "overall_ready_for_exact_clone": repair_loop.get("overall_ready_for_exact_clone"),
                        "stop_reason": repair_loop.get("stop_reason"),
                        "persisted": repair_loop.get("persisted"),
                        "note": repair_loop.get("note"),
                    }
                    persisted["repair_passes"] = repair_loop.get("persisted")
            plan_path = persisted.get("plan")
            if isinstance(plan_path, str):
                Path(plan_path).write_text(json.dumps(result, indent=2) + "\n")

    return result


def persist_reproduction_bundle(output_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    reproduction_dir = output_dir / "reproduction"
    reproduction_dir.mkdir(parents=True, exist_ok=True)

    persisted: dict[str, str] = {}
    plan_path = reproduction_dir / "plan.json"
    plan_path.write_text(json.dumps(result, indent=2) + "\n")
    persisted["plan"] = str(plan_path)

    rebuild_prompt = result.get("rebuild_prompt")
    if isinstance(rebuild_prompt, str) and rebuild_prompt.strip():
        rebuild_prompt_path = reproduction_dir / "rebuild-prompt.txt"
        rebuild_prompt_path.write_text(rebuild_prompt.rstrip() + "\n")
        persisted["rebuild_prompt"] = str(rebuild_prompt_path)

    rebuild_scaffold = result.get("rebuild_scaffold")
    if isinstance(rebuild_scaffold, dict):
        persisted["rebuild_scaffold"] = persist_rebuild_scaffold(reproduction_dir, rebuild_scaffold)

    exact_reuse = result.get("exact_reuse")
    if isinstance(exact_reuse, dict):
        snippets = exact_reuse.get("snippets", {})
        html_path = reproduction_dir / "embed.html"
        html_path.write_text(snippets.get("html", "") + "\n")
        persisted["embed_html"] = str(html_path)

        nextjs_path = reproduction_dir / "embed.tsx"
        nextjs_path.write_text(snippets.get("nextjs", "") + "\n")
        persisted["embed_tsx"] = str(nextjs_path)

        prompt_path = reproduction_dir / "prompt.txt"
        prompt_path.write_text(
            "\n".join(
                [
                    "Use the original exact reuse target instead of rebuilding this reference.",
                    f"Platform: {exact_reuse.get('platform')}",
                    f"Kind: {exact_reuse.get('kind')}",
                    f"URL: {exact_reuse.get('url')}",
                ]
            )
            + "\n"
        )
        persisted["prompt"] = str(prompt_path)

    return persisted


def _preferred_renderer_hint(rebuild_artifacts: dict[str, str]) -> dict[str, str] | None:
    if not isinstance(rebuild_artifacts, dict):
        return None

    next_runtime_keys = (
        "next-app/app/layout.tsx",
        "next-app/app/page.tsx",
        "next-app/app/globals.css",
        "next-app/components/BoundedReferencePage.tsx",
        "next-app/components/reference-data.ts",
    )
    if all(rebuild_artifacts.get(key) for key in next_runtime_keys):
        return {
            "name": "next-runtime-app",
            "reason": "Latest generated next-app scaffold is present, so the booted Next runtime should be verified first.",
            "entrypoint": rebuild_artifacts["next-app/app/page.tsx"],
        }

    if rebuild_artifacts.get("app-preview.html"):
        return {
            "name": "role-inferred-app",
            "reason": "Role-inferred app preview is the freshest bounded renderer before the low-level starter scaffold.",
            "entrypoint": rebuild_artifacts["app-preview.html"],
        }

    if rebuild_artifacts.get("starter.html"):
        return {
            "name": "starter",
            "reason": "Starter scaffold is the only available renderer entrypoint.",
            "entrypoint": rebuild_artifacts["starter.html"],
        }

    return None
