"""Reproduction bundle generation for source-first clone workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .planning import plan_reproduction_path
from .rebuild_scaffold import build_rebuild_scaffold, persist_rebuild_scaffold


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
    if frame_policy.get("embeddable") is True and static.get("final_url"):
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
        "generic-embed": 7,
        "iframe-src": 8,
        "spline-code": 9,
        "runtime-hint": 10,
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
    assets_capture = captures.get("assets", {}) if isinstance(captures, dict) else {}
    interactions_capture = captures.get("interactions", {}) if isinstance(captures, dict) else {}
    interaction_trace_capture = captures.get("interactionTrace", {}) if isinstance(captures, dict) else {}

    prompt_lines = [
        "Rebuild this reference as faithfully as possible using the captured structure and styling summary.",
        f"Reference URL: {capture_bundle.get('url')}",
        f"Final URL: {static.get('final_url')}",
    ]

    if static.get("title"):
        prompt_lines.append(f"Page title: {static['title']}")
    if meta.get("description"):
        prompt_lines.append(f"Description: {meta['description']}")

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
        capture_bundle=capture_bundle,
    )

    result: dict[str, Any] = {
        "policy_mode": policy.get("mode"),
        "plan": plan,
        "candidate_count": len(candidates),
        "candidates": candidates[:40],
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
        persisted = persist_reproduction_bundle(Path(output_dir).expanduser().resolve(), result)
        result["persisted"] = persisted

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
