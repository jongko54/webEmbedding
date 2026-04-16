"""Bounded rebuild scaffold generation for frame-blocked references."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


SCAFFOLD_SCHEMA_VERSION = "0.1.0"


def _clean_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _is_transparent(color: str | None) -> bool:
    if not color:
        return True
    lowered = color.strip().lower()
    return lowered in {"transparent", "rgba(0, 0, 0, 0)", "rgba(0,0,0,0)", "none"}


def _get_capture_sections(capture_bundle: dict[str, Any]) -> dict[str, Any]:
    static = capture_bundle.get("static", {}) if isinstance(capture_bundle, dict) else {}
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    session_request = capture_bundle.get("session_request", {}) if isinstance(capture_bundle, dict) else {}

    return {
        "static": static if isinstance(static, dict) else {},
        "policy": capture_bundle.get("policy", {}) if isinstance(capture_bundle, dict) else {},
        "runtime": runtime if isinstance(runtime, dict) else {},
        "captures": captures if isinstance(captures, dict) else {},
        "session_request": session_request if isinstance(session_request, dict) else {},
    }


def _collect_dom_outline(node: dict[str, Any] | None, bucket: list[dict[str, Any]], depth: int = 0, limit: int = 12) -> None:
    if not isinstance(node, dict) or len(bucket) >= limit:
        return
    node_type = node.get("type")
    if node_type == "element":
        text = _clean_text(node.get("text"), 120)
        tag = _clean_text(node.get("tag"), 40)
        if text or tag:
            bucket.append(
                {
                    "depth": depth,
                    "tag": tag or "element",
                    "id": _clean_text(node.get("id"), 80) or None,
                    "className": _clean_text(node.get("className"), 120) or None,
                    "role": _clean_text(node.get("role"), 60) or None,
                    "text": text or None,
                }
            )
        for child in node.get("children", []) or []:
            _collect_dom_outline(child, bucket, depth=depth + 1, limit=limit)
            if len(bucket) >= limit:
                break
    elif node_type == "text":
        text = _clean_text(node.get("text"), 120)
        if text:
            bucket.append({"depth": depth, "tag": "#text", "text": text})


def _collect_style_blocks(style_entries: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, entry in enumerate(style_entries):
        if not isinstance(entry, dict):
            continue
        rect = entry.get("rect", {}) if isinstance(entry.get("rect", {}), dict) else {}
        styles = entry.get("styles", {}) if isinstance(entry.get("styles", {}), dict) else {}
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        text = _clean_text(entry.get("text"), 140)
        blocks.append(
            {
                "index": index,
                "tag": _clean_text(entry.get("tag"), 40) or "div",
                "text": text or None,
                "rect": {
                    "x": int(rect.get("x") or 0),
                    "y": int(rect.get("y") or 0),
                    "width": width,
                    "height": height,
                },
                "styles": {
                    "display": styles.get("display"),
                    "position": styles.get("position"),
                    "color": styles.get("color"),
                    "backgroundColor": styles.get("backgroundColor"),
                    "fontFamily": styles.get("fontFamily"),
                    "fontSize": styles.get("fontSize"),
                    "fontWeight": styles.get("fontWeight"),
                    "lineHeight": styles.get("lineHeight"),
                    "borderRadius": styles.get("borderRadius"),
                    "opacity": styles.get("opacity"),
                },
            }
        )
        if len(blocks) >= limit:
            break
    return blocks


def _collect_unique(values: list[str | None], limit: int = 4) -> list[str]:
    unique: list[str] = []
    for value in values:
        if not value:
            continue
        cleaned = " ".join(str(value).split())
        if cleaned in unique:
            continue
        unique.append(cleaned)
        if len(unique) >= limit:
            break
    return unique


def _derive_palette(style_entries: list[dict[str, Any]]) -> dict[str, str | None]:
    colors: list[str | None] = []
    backgrounds: list[str | None] = []
    for entry in style_entries:
        if not isinstance(entry, dict):
            continue
        styles = entry.get("styles", {}) if isinstance(entry.get("styles", {}), dict) else {}
        color = styles.get("color")
        background = styles.get("backgroundColor")
        if color and not _is_transparent(color):
            colors.append(str(color))
        if background and not _is_transparent(background):
            backgrounds.append(str(background))

    text_colors = _collect_unique(colors, limit=2)
    background_colors = _collect_unique(backgrounds, limit=2)
    accent_candidates = [color for color in colors if color and color != (text_colors[0] if text_colors else None)]
    accent_colors = _collect_unique(accent_candidates, limit=1)

    return {
        "text": text_colors[0] if text_colors else None,
        "accent": accent_colors[0] if accent_colors else None,
        "surface": background_colors[0] if background_colors else None,
        "surface_alt": background_colors[1] if len(background_colors) > 1 else None,
    }


def _derive_typography(style_entries: list[dict[str, Any]]) -> dict[str, Any]:
    fonts: list[str | None] = []
    sizes: list[str | None] = []
    weights: list[str | None] = []
    for entry in style_entries:
        if not isinstance(entry, dict):
            continue
        styles = entry.get("styles", {}) if isinstance(entry.get("styles", {}), dict) else {}
        fonts.append(styles.get("fontFamily"))
        sizes.append(styles.get("fontSize"))
        weights.append(styles.get("fontWeight"))
    return {
        "fonts": _collect_unique(fonts, limit=3),
        "sizes": _collect_unique(sizes, limit=4),
        "weights": _collect_unique(weights, limit=4),
    }


def _render_css(summary: dict[str, Any]) -> str:
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    typography = summary.get("typography", {}) if isinstance(summary, dict) else {}
    base_font = (typography.get("fonts") or ["Inter, system-ui, sans-serif"])[0]
    text_color = palette.get("text") or "#e5e7eb"
    surface_color = palette.get("surface") or "#111827"
    surface_alt = palette.get("surface_alt") or "#1f2937"
    accent = palette.get("accent") or "#7c3aed"
    return "\n".join(
        [
            ":root {",
            f"  --bg: {surface_color};",
            f"  --bg-alt: {surface_alt};",
            f"  --text: {text_color};",
            f"  --accent: {accent};",
            "  --muted: rgba(229, 231, 235, 0.72);",
            "  --border: rgba(255, 255, 255, 0.10);",
            f"  --font-sans: {base_font};",
            "}",
            "",
            "* { box-sizing: border-box; }",
            "html, body { min-height: 100%; }",
            "body {",
            "  margin: 0;",
            "  color: var(--text);",
            "  font-family: var(--font-sans);",
            "  background:",
            "    radial-gradient(circle at top left, rgba(124, 58, 237, 0.18), transparent 30%),",
            "    linear-gradient(180deg, #070b14 0%, var(--bg) 100%);",
            "}",
            "a { color: inherit; }",
            ".shell {",
            "  max-width: 1200px;",
            "  margin: 0 auto;",
            "  padding: 40px 20px 56px;",
            "}",
            ".hero, .panel {",
            "  border: 1px solid var(--border);",
            "  background: rgba(255, 255, 255, 0.04);",
            "  backdrop-filter: blur(14px);",
            "  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.22);",
            "}",
            ".hero {",
            "  border-radius: 28px;",
            "  padding: 28px;",
            "  margin-bottom: 24px;",
            "}",
            ".eyebrow {",
            "  margin: 0 0 12px;",
            "  text-transform: uppercase;",
            "  letter-spacing: 0.18em;",
            "  font-size: 12px;",
            "  color: var(--muted);",
            "}",
            "h1 {",
            "  margin: 0;",
            "  font-size: clamp(2rem, 4vw, 4rem);",
            "  line-height: 0.96;",
            "  letter-spacing: -0.04em;",
            "}",
            ".lede {",
            "  max-width: 64ch;",
            "  margin: 16px 0 0;",
            "  font-size: 1rem;",
            "  line-height: 1.6;",
            "  color: var(--muted);",
            "}",
            ".meta {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  gap: 12px;",
            "  margin-top: 20px;",
            "  color: var(--muted);",
            "  font-size: 0.9rem;",
            "}",
            ".grid {",
            "  display: grid;",
            "  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));",
            "  gap: 18px;",
            "}",
            ".card {",
            "  border-radius: 22px;",
            "  padding: 18px;",
            "}",
            ".card h2 {",
            "  margin: 0 0 8px;",
            "  font-size: 1rem;",
            "}",
            ".card p {",
            "  margin: 0;",
            "  color: var(--muted);",
            "  line-height: 1.55;",
            "}",
            ".card code {",
            "  display: inline-block;",
            "  margin-top: 12px;",
            "  padding: 6px 10px;",
            "  border-radius: 999px;",
            "  background: rgba(124, 58, 237, 0.16);",
            "  color: #f5f3ff;",
            "  font-size: 12px;",
            "}",
            ".footer {",
            "  margin-top: 24px;",
            "  color: var(--muted);",
            "  font-size: 0.9rem;",
            "}",
        ]
    )


def _render_html(summary: dict[str, Any]) -> str:
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    title = escape(str(summary.get("title") or "Captured reference"))
    subtitle = escape(
        str(
            summary.get("description")
            or "Bounded rebuild scaffold derived from DOM and style capture. Use this as a starter, not a claim of exact fidelity."
        )
    )
    footer_bits = [
        f"frame policy: {(summary.get('frame_policy', {}) or {}).get('reason') or 'unknown'}",
        f"assets: {summary.get('assets', {}).get('image_count', 0)} images",
        f"interactive states: {summary.get('interactions', {}).get('count', 0)}",
    ]
    cards: list[str] = []
    for block in blocks:
        rect = block.get("rect", {}) if isinstance(block, dict) else {}
        styles = block.get("styles", {}) if isinstance(block, dict) else {}
        label = escape(str(block.get("tag") or "div"))
        text = escape(str(block.get("text") or ""))
        meta = escape(
            f"{rect.get('width', 0)} x {rect.get('height', 0)} px"
        )
        details = []
        for field in ("fontFamily", "fontSize", "fontWeight", "color", "backgroundColor"):
            value = styles.get(field)
            if value:
                details.append(escape(str(value)))
        cards.append(
            "\n".join(
                [
                    '<article class="card panel">',
                    f"  <h2>{label}</h2>",
                    f"  <p>{text or 'Layout block derived from capture data.'}</p>",
                    f"  <code>{meta}</code>",
                    f"  <p>{' · '.join(details) if details else 'No computed style sample available.'}</p>",
                    "</article>",
                ]
            )
        )

    if not cards:
        cards.append(
            "\n".join(
                [
                    '<article class="card panel">',
                    "  <h2>Captured shell</h2>",
                    "  <p>No rich DOM/style data was available, so this scaffold keeps a single neutral container and metadata block.</p>",
                    "  <code>Fallback state</code>",
                    "</article>",
                ]
            )
        )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"  <title>{title}</title>",
            '  <link rel="stylesheet" href="./starter.css" />',
            "</head>",
            "<body>",
            '  <main class="shell">',
            '    <section class="hero panel">',
            '      <p class="eyebrow">Rebuild scaffold</p>',
            f"      <h1>{title}</h1>",
            f"      <p class=\"lede\">{subtitle}</p>",
            '      <div class="meta">',
            *[f"        <span>{escape(bit)}</span>" for bit in footer_bits],
            "      </div>",
            "    </section>",
            '    <section class="grid">',
            *[f"      {card}" for card in cards],
            "    </section>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )


def _render_tsx(summary: dict[str, Any]) -> str:
    title = str(summary.get("title") or "Captured reference")
    subtitle = str(
        summary.get("description")
        or "Bounded rebuild scaffold derived from DOM and style capture. Use this as a starter, not a claim of exact fidelity."
    )
    footer_bits = [
        f"frame policy: {(summary.get('frame_policy', {}) or {}).get('reason') or 'unknown'}",
        f"assets: {summary.get('assets', {}).get('image_count', 0)} images",
        f"interactive states: {summary.get('interactions', {}).get('count', 0)}",
    ]
    cards: list[dict[str, str]] = []
    for block in summary.get("blocks", []) if isinstance(summary, dict) else []:
        rect = block.get("rect", {}) if isinstance(block, dict) else {}
        styles = block.get("styles", {}) if isinstance(block, dict) else {}
        detail_parts = [
            str(styles.get(field))
            for field in ("fontFamily", "fontSize", "fontWeight", "color", "backgroundColor")
            if styles.get(field)
        ]
        cards.append(
            {
                "tag": str(block.get("tag") or "div"),
                "text": str(block.get("text") or "Layout block derived from capture data."),
                "meta": f"{rect.get('width', 0)} x {rect.get('height', 0)} px",
                "details": " · ".join(detail_parts) if detail_parts else "No computed style sample available.",
            }
        )

    if not cards:
        cards.append(
            {
                "tag": "Captured shell",
                "text": "No rich DOM/style data was available, so this scaffold keeps a single neutral container and metadata block.",
                "meta": "Fallback state",
                "details": "No computed style sample available.",
            }
        )

    cards_literal = json.dumps(cards, ensure_ascii=False, indent=2)
    footer_literal = json.dumps(footer_bits, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            'import "./starter.css";',
            "",
            f"const metaBits = {footer_literal} as const;",
            f"const cards = {cards_literal} as const;",
            "",
            "export default function RebuildStarter() {",
            "  return (",
            '    <main className="shell">',
            '      <section className="hero panel">',
            '        <p className="eyebrow">Rebuild scaffold</p>',
            f"        <h1>{escape(title)}</h1>",
            f"        <p className=\"lede\">{escape(subtitle)}</p>",
            '        <div className="meta">',
            '          {metaBits.map((bit) => (',
            '            <span key={bit}>{bit}</span>',
            "          ))}",
            "        </div>",
            "      </section>",
            '      <section className="grid">',
            '        {cards.map((card, index) => (',
            '          <article className="card panel" key={`${card.tag}-${index}`}>',
            "            <h2>{card.tag}</h2>",
            "            <p>{card.text}</p>",
            "            <code>{card.meta}</code>",
            "            <p>{card.details}</p>",
            "          </article>",
            "        ))}",
            "      </section>",
            "    </main>",
            "  );",
            "}",
        ]
    )


def _render_prompt(summary: dict[str, Any]) -> str:
    lines = [
        "Use the scaffold as a bounded rebuild starter, not as an exact reproduction claim.",
        f"Source URL: {summary.get('source_url')}",
        f"Final URL: {summary.get('final_url')}",
        f"Frame policy: {(summary.get('frame_policy', {}) or {}).get('reason')}",
        "Preserve hierarchy, spacing, and visual rhythm from the captured blocks.",
        "Do not expand beyond the captured structure unless the implementation needs a minimal wrapper.",
    ]
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    if blocks:
        lines.append("Primary captured blocks:")
        for block in blocks[:6]:
            label = block.get("tag") or "div"
            text = block.get("text") or ""
            size = block.get("rect", {})
            lines.append(f"- {label} {size.get('width', 0)}x{size.get('height', 0)} {text}".strip())
    return "\n".join(lines)


def build_rebuild_scaffold(capture_bundle: dict[str, Any]) -> dict[str, Any]:
    sections = _get_capture_sections(capture_bundle)
    static = sections["static"]
    policy = sections["policy"]
    runtime = sections["runtime"]
    captures = sections["captures"]
    session_request = sections["session_request"]
    dom_capture = captures.get("dom", {}) if isinstance(captures, dict) else {}
    styles_capture = captures.get("styles", {}) if isinstance(captures, dict) else {}
    assets_capture = captures.get("assets", {}) if isinstance(captures, dict) else {}
    interactions_capture = captures.get("interactions", {}) if isinstance(captures, dict) else {}

    style_entries = styles_capture.get("content", []) if isinstance(styles_capture, dict) else []
    if not isinstance(style_entries, list):
        style_entries = []
    blocks = _collect_style_blocks(style_entries)
    outline: list[dict[str, Any]] = []
    if dom_capture.get("available"):
        _collect_dom_outline(dom_capture.get("content"), outline)

    asset_content = assets_capture.get("content", {}) if isinstance(assets_capture, dict) else {}
    image_count = len(asset_content.get("images", []) or []) if isinstance(asset_content, dict) else 0
    script_count = len(asset_content.get("scripts", []) or []) if isinstance(asset_content, dict) else 0
    iframe_count = len(asset_content.get("iframes", []) or []) if isinstance(asset_content, dict) else 0

    interaction_entries = interactions_capture.get("content", []) if isinstance(interactions_capture, dict) else []
    interaction_sample: list[dict[str, Any]] = []
    for entry in interaction_entries[:4] if isinstance(interaction_entries, list) else []:
        if not isinstance(entry, dict):
            continue
        interaction_sample.append(
            {
                "tag": entry.get("tag"),
                "text": _clean_text(entry.get("text"), 120) or None,
                "href": entry.get("href"),
                "rect": entry.get("rect"),
                "hoverDeltaKeys": sorted((entry.get("hoverDelta") or {}).keys()) if isinstance(entry.get("hoverDelta"), dict) else [],
                "focusDeltaKeys": sorted((entry.get("focusDelta") or {}).keys()) if isinstance(entry.get("focusDelta"), dict) else [],
            }
        )

    frame_policy = static.get("frame_policy", {}) if isinstance(static.get("frame_policy", {}), dict) else {}
    meta = static.get("meta", {}) if isinstance(static.get("meta", {}), dict) else {}

    summary = {
        "schema_version": SCAFFOLD_SCHEMA_VERSION,
        "coverage": "bounded-rebuild-scaffold",
        "source_url": capture_bundle.get("url"),
        "final_url": static.get("final_url"),
        "title": static.get("title") or "Captured reference",
        "description": meta.get("description"),
        "policy_mode": policy.get("mode"),
        "frame_policy": frame_policy,
        "viewport": {
            "width": session_request.get("viewport_width"),
            "height": session_request.get("viewport_height"),
        },
        "signals": {
            "dom_available": bool(dom_capture.get("available")),
            "styles_available": bool(styles_capture.get("available")),
            "assets_available": bool(assets_capture.get("available")),
            "interactions_available": bool(interactions_capture.get("available")),
            "runtime_available": bool(runtime.get("available")),
        },
        "outline": outline[:12],
        "blocks": blocks,
        "palette": _derive_palette(style_entries),
        "typography": _derive_typography(style_entries),
        "assets": {
            "image_count": image_count,
            "script_count": script_count,
            "iframe_count": iframe_count,
        },
        "interactions": {
            "count": len(interaction_entries) if isinstance(interaction_entries, list) else 0,
            "sample": interaction_sample,
        },
        "note": "This scaffold is intentionally bounded. It is a starter for reconstruction when an exact reuse path is unavailable.",
    }
    html = _render_html(summary)
    css = _render_css(summary)
    tsx = _render_tsx(summary)
    prompt = _render_prompt(summary)

    artifacts = {
        "layout-summary.json": summary,
        "starter.html": html,
        "starter.css": css,
        "starter.tsx": tsx,
        "prompt.txt": prompt,
        "manifest.json": {
            "schema_version": SCAFFOLD_SCHEMA_VERSION,
            "coverage": summary["coverage"],
            "files": ["layout-summary.json", "starter.html", "starter.css", "starter.tsx", "prompt.txt"],
        },
    }

    return {
        "available": True,
        "status": "generated",
        "bounded": True,
        "reason": "Exact reuse unavailable, so a bounded rebuild scaffold was derived from the capture bundle.",
        "summary": summary,
        "artifacts": artifacts,
    }


def persist_rebuild_scaffold(output_dir: Path, scaffold: dict[str, Any]) -> dict[str, str]:
    rebuild_dir = output_dir / "rebuild"
    rebuild_dir.mkdir(parents=True, exist_ok=True)

    persisted: dict[str, str] = {}
    artifacts = scaffold.get("artifacts", {}) if isinstance(scaffold, dict) else {}
    if not isinstance(artifacts, dict):
        artifacts = {}

    for filename, content in artifacts.items():
        target = rebuild_dir / filename
        if filename.endswith(".json"):
            target.write_text(json.dumps(content, indent=2) + "\n")
        elif isinstance(content, str):
            target.write_text(content.rstrip() + "\n")
        else:
            target.write_text(str(content).rstrip() + "\n")
        persisted[filename] = str(target)

    return persisted
