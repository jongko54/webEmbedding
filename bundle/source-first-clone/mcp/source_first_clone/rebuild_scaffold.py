"""Bounded rebuild scaffold generation for frame-blocked references."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


SCAFFOLD_SCHEMA_VERSION = "0.1.0"
NOISY_TAGS = {"script", "style", "meta", "link", "noscript"}


def _clean_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _looks_like_code_noise(text: str) -> bool:
    lowered = text.lower()
    if len(text) > 72 and ("{" in text or "function(" in lowered or "window." in lowered):
        return True
    if len(text) > 72 and text.count(";") >= 2 and text.count(":") >= 2:
        return True
    if lowered.startswith((".", "#")) and "{" in lowered:
        return True
    return False


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
        if tag.lower() in NOISY_TAGS:
            return
        if _looks_like_code_noise(text):
            text = ""
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
        tag = _clean_text(entry.get("tag"), 40) or "div"
        if tag.lower() in NOISY_TAGS:
            continue
        text = _clean_text(entry.get("text"), 140)
        if _looks_like_code_noise(text):
            text = ""
        blocks.append(
            {
                "index": index,
                "tag": tag,
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


def _block_title(block: dict[str, Any], index: int) -> str:
    text = _clean_text(block.get("text"), 64)
    if text:
        words = text.split()
        return " ".join(words[:6])
    tag = _clean_text(block.get("tag"), 24)
    if tag:
        return f"{tag.title()} block {index + 1}"
    return f"Section {index + 1}"


def _block_copy(block: dict[str, Any]) -> str:
    text = _clean_text(block.get("text"), 180)
    if text:
        return text
    return "Captured layout block derived from the source page structure."


def _interaction_label(entry: dict[str, Any], index: int) -> str:
    text = _clean_text(entry.get("text"), 56)
    if text:
        return text
    tag = _clean_text(entry.get("tag"), 24) or "element"
    return f"{tag.title()} interaction {index + 1}"


def _build_app_model(summary: dict[str, Any]) -> dict[str, Any]:
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    outline = summary.get("outline", []) if isinstance(summary, dict) else []
    interactions = (summary.get("interactions", {}) or {}).get("sample", [])
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    typography = summary.get("typography", {}) if isinstance(summary, dict) else {}

    section_cards: list[dict[str, Any]] = []
    for index, block in enumerate(blocks[:8]):
        if not isinstance(block, dict):
            continue
        rect = block.get("rect", {}) if isinstance(block.get("rect", {}), dict) else {}
        styles = block.get("styles", {}) if isinstance(block.get("styles", {}), dict) else {}
        detail_parts = [
            str(styles.get(field))
            for field in ("fontFamily", "fontSize", "fontWeight", "color", "backgroundColor")
            if styles.get(field)
        ]
        section_cards.append(
            {
                "id": f"section-{index + 1}",
                "title": _block_title(block, index),
                "tag": str(block.get("tag") or "div"),
                "copy": _block_copy(block),
                "meta": f"{rect.get('width', 0)} x {rect.get('height', 0)} px",
                "details": detail_parts,
            }
        )

    if not section_cards:
        section_cards.append(
            {
                "id": "section-1",
                "title": "Captured shell",
                "tag": "div",
                "copy": "No rich DOM/style data was available, so this scaffold keeps a neutral shell and leaves the exact rebuild to downstream implementation.",
                "meta": "Fallback state",
                "details": [],
            }
        )

    interaction_cards: list[dict[str, Any]] = []
    for index, entry in enumerate(interactions[:6] if isinstance(interactions, list) else []):
        if not isinstance(entry, dict):
            continue
        states: list[str] = []
        hover_keys = entry.get("hoverDeltaKeys", [])
        focus_keys = entry.get("focusDeltaKeys", [])
        if hover_keys:
            states.append(f"hover: {', '.join(str(key) for key in hover_keys)}")
        if focus_keys:
            states.append(f"focus: {', '.join(str(key) for key in focus_keys)}")
        interaction_cards.append(
            {
                "id": f"interaction-{index + 1}",
                "label": _interaction_label(entry, index),
                "copy": _clean_text(entry.get("text"), 140) or "Interactive element sampled from runtime capture.",
                "tag": str(entry.get("tag") or "element"),
                "href": entry.get("href"),
                "states": states or ["interaction detected"],
            }
        )

    outline_cards: list[dict[str, Any]] = []
    for index, entry in enumerate(outline[:8] if isinstance(outline, list) else []):
        if not isinstance(entry, dict):
            continue
        descriptor = " ".join(
            part
            for part in [
                str(entry.get("tag") or ""),
                str(entry.get("role") or ""),
                str(entry.get("id") or ""),
                str(entry.get("className") or ""),
            ]
            if part
        ).strip()
        outline_cards.append(
            {
                "id": f"outline-{index + 1}",
                "label": descriptor or f"Node {index + 1}",
                "copy": _clean_text(entry.get("text"), 140) or "Structural node captured from the DOM outline.",
                "depth": entry.get("depth", 0),
            }
        )

    meta_bits = [
        f"frame policy: {(summary.get('frame_policy', {}) or {}).get('reason') or 'unknown'}",
        f"blocks: {len(section_cards)}",
        f"assets: {(summary.get('assets', {}) or {}).get('image_count', 0)} images / {(summary.get('assets', {}) or {}).get('script_count', 0)} scripts",
        f"interactive states: {(summary.get('interactions', {}) or {}).get('count', 0)}",
    ]
    signal_bits = [
        label.replace("_", " ")
        for label, enabled in ((summary.get("signals", {}) or {}).items())
        if enabled
    ]

    subtitle = (
        summary.get("description")
        or "Bounded rebuild scaffold derived from DOM, style, asset, and interaction capture. Use it as a practical app starter, not an exact reproduction claim."
    )

    return {
        "title": str(summary.get("title") or "Captured reference"),
        "subtitle": str(subtitle),
        "metaBits": meta_bits,
        "signalBits": signal_bits[:6],
        "viewport": summary.get("viewport"),
        "palette": {
            "text": palette.get("text"),
            "accent": palette.get("accent"),
            "surface": palette.get("surface"),
            "surfaceAlt": palette.get("surface_alt"),
        },
        "typography": {
            "fonts": typography.get("fonts") or [],
            "sizes": typography.get("sizes") or [],
            "weights": typography.get("weights") or [],
        },
        "sections": section_cards,
        "interactions": interaction_cards,
        "outline": outline_cards,
        "note": str(summary.get("note") or ""),
    }


def _render_reference_data_ts(app_model: dict[str, Any]) -> str:
    model_literal = json.dumps(app_model, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            f"export const boundedReferenceData = {model_literal} as const;",
            "",
            "export type BoundedReferenceData = typeof boundedReferenceData;",
        ]
    )


def _render_bounded_reference_page_tsx() -> str:
    return "\n".join(
        [
            'import type { BoundedReferenceData } from "./reference-data";',
            "",
            "type Props = {",
            "  data: BoundedReferenceData;",
            "};",
            "",
            "export function BoundedReferencePage({ data }: Props) {",
            "  return (",
            '    <main className=\"bounded-shell\">',
            '      <section className=\"bounded-hero bounded-panel\">',
            '        <p className=\"bounded-eyebrow\">Bounded rebuild app scaffold</p>',
            "        <h1>{data.title}</h1>",
            '        <p className=\"bounded-lede\">{data.subtitle}</p>',
            '        <div className=\"bounded-meta\">',
            "          {data.metaBits.map((bit) => (",
            '            <span className=\"bounded-chip\" key={bit}>',
            "              {bit}",
            "            </span>",
            "          ))}",
            "        </div>",
            "      </section>",
            "",
            '      <section className=\"bounded-layout\">',
            '        <div className=\"bounded-main\">',
            '          <div className=\"bounded-section-grid\">',
            "            {data.sections.map((section) => (",
            '              <article className=\"bounded-card bounded-panel\" key={section.id}>',
            '                <p className=\"bounded-kicker\">{section.tag}</p>',
            "                <h2>{section.title}</h2>",
            '                <p className=\"bounded-copy\">{section.copy}</p>',
            '                <div className=\"bounded-meta bounded-meta--inline\">',
            '                  <span className=\"bounded-chip\">{section.meta}</span>',
            "                  {section.details.slice(0, 3).map((detail) => (",
            '                    <span className=\"bounded-chip bounded-chip--muted\" key={detail}>',
            "                      {detail}",
            "                    </span>",
            "                  ))}",
            "                </div>",
            "              </article>",
            "            ))}",
            "          </div>",
            "        </div>",
            "",
            '        <aside className=\"bounded-rail\">',
            '          <section className=\"bounded-panel bounded-stack\">',
            '            <p className=\"bounded-kicker\">Signals</p>',
            '            <ul className=\"bounded-list\">',
            "              {data.signalBits.length ? (",
            "                data.signalBits.map((signal) => <li key={signal}>{signal}</li>)",
            "              ) : (",
            "                <li>No extra runtime signals were captured.</li>",
            "              )}",
            "            </ul>",
            "          </section>",
            "",
            '          <section className=\"bounded-panel bounded-stack\">',
            '            <p className=\"bounded-kicker\">Interaction samples</p>',
            '            <div className=\"bounded-stack\">',
            "              {data.interactions.length ? (",
            "                data.interactions.map((entry) => (",
            '                  <article className=\"bounded-mini-card\" key={entry.id}>',
            '                    <strong>{entry.label}</strong>',
            '                    <p>{entry.copy}</p>',
            '                    <div className=\"bounded-meta bounded-meta--inline\">',
            "                      {entry.states.map((state) => (",
            '                        <span className=\"bounded-chip bounded-chip--muted\" key={state}>',
            "                          {state}",
            "                        </span>",
            "                      ))}",
            "                    </div>",
            "                  </article>",
            "                ))",
            "              ) : (",
            '                <article className=\"bounded-mini-card\">',
            "                  <strong>No sampled interactions</strong>",
            "                  <p>Interaction data was not available in the capture bundle.</p>",
            "                </article>",
            "              )}",
            "            </div>",
            "          </section>",
            "",
            '          <section className=\"bounded-panel bounded-stack\">',
            '            <p className=\"bounded-kicker\">DOM outline</p>',
            '            <div className=\"bounded-stack bounded-stack--tight\">',
            "              {data.outline.slice(0, 6).map((item) => (",
            '                <article className=\"bounded-outline-item\" key={item.id}>',
            '                  <strong>{item.label}</strong>',
            '                  <p>{item.copy}</p>',
            "                </article>",
            "              ))}",
            "            </div>",
            "          </section>",
            "        </aside>",
            "      </section>",
            "    </main>",
            "  );",
            "}",
        ]
    )


def _render_next_app_page_tsx() -> str:
    return "\n".join(
        [
            'import { BoundedReferencePage } from "../components/BoundedReferencePage";',
            'import { boundedReferenceData } from "../components/reference-data";',
            "",
            "export default function Page() {",
            "  return <BoundedReferencePage data={boundedReferenceData} />;",
            "}",
        ]
    )


def _render_next_app_layout_tsx(summary: dict[str, Any]) -> str:
    title = json.dumps(str(summary.get("title") or "Captured reference"), ensure_ascii=False)
    description = json.dumps(
        str(
            summary.get("description")
            or "Bounded rebuild scaffold derived from capture data."
        ),
        ensure_ascii=False,
    )
    return "\n".join(
        [
            'import "./globals.css";',
            'import type { Metadata } from "next";',
            'import type { ReactNode } from "react";',
            "",
            "export const metadata: Metadata = {",
            f"  title: {title},",
            f"  description: {description},",
            "  robots: {",
            "    index: false,",
            "    follow: false,",
            "  },",
            "};",
            "",
            "export default function RootLayout({ children }: { children: ReactNode }) {",
            "  return (",
            '    <html lang="en">',
            "      <body>{children}</body>",
            "    </html>",
            "  );",
            "}",
        ]
    )


def _render_next_app_globals_css(summary: dict[str, Any]) -> str:
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    typography = summary.get("typography", {}) if isinstance(summary, dict) else {}
    base_font = (typography.get("fonts") or ["Inter, system-ui, sans-serif"])[0]
    text_color = palette.get("text") or "#e5e7eb"
    surface_color = palette.get("surface") or "#0f172a"
    surface_alt = palette.get("surface_alt") or "#172033"
    accent = palette.get("accent") or "#7c3aed"
    return "\n".join(
        [
            ":root {",
            f"  --bounded-bg: {surface_color};",
            f"  --bounded-bg-alt: {surface_alt};",
            f"  --bounded-text: {text_color};",
            f"  --bounded-accent: {accent};",
            "  --bounded-muted: rgba(226, 232, 240, 0.72);",
            "  --bounded-border: rgba(255, 255, 255, 0.12);",
            f"  --bounded-font-sans: {base_font};",
            "}",
            "",
            "* { box-sizing: border-box; }",
            "html, body { min-height: 100%; }",
            "body {",
            "  margin: 0;",
            "  color: var(--bounded-text);",
            "  font-family: var(--bounded-font-sans);",
            "  background:",
            "    radial-gradient(circle at top left, color-mix(in srgb, var(--bounded-accent) 18%, transparent), transparent 34%),",
            "    linear-gradient(180deg, #060911 0%, var(--bounded-bg) 100%);",
            "}",
            ".bounded-shell {",
            "  max-width: 1280px;",
            "  margin: 0 auto;",
            "  padding: 40px 20px 72px;",
            "}",
            ".bounded-layout {",
            "  display: grid;",
            "  grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.9fr);",
            "  gap: 20px;",
            "  align-items: start;",
            "}",
            ".bounded-main, .bounded-rail { min-width: 0; }",
            ".bounded-section-grid {",
            "  display: grid;",
            "  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));",
            "  gap: 18px;",
            "}",
            ".bounded-panel {",
            "  border: 1px solid var(--bounded-border);",
            "  border-radius: 24px;",
            "  background: rgba(255, 255, 255, 0.04);",
            "  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.22);",
            "  backdrop-filter: blur(14px);",
            "}",
            ".bounded-hero {",
            "  padding: 28px;",
            "  margin-bottom: 20px;",
            "}",
            ".bounded-eyebrow, .bounded-kicker {",
            "  margin: 0 0 10px;",
            "  text-transform: uppercase;",
            "  letter-spacing: 0.16em;",
            "  font-size: 12px;",
            "  color: var(--bounded-muted);",
            "}",
            ".bounded-hero h1, .bounded-card h2 {",
            "  margin: 0;",
            "  letter-spacing: -0.04em;",
            "}",
            ".bounded-hero h1 {",
            "  font-size: clamp(2.2rem, 4vw, 4.4rem);",
            "  line-height: 0.94;",
            "}",
            ".bounded-lede, .bounded-copy, .bounded-mini-card p, .bounded-outline-item p {",
            "  color: var(--bounded-muted);",
            "  line-height: 1.6;",
            "}",
            ".bounded-lede {",
            "  max-width: 62ch;",
            "  margin: 16px 0 0;",
            "}",
            ".bounded-meta {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  gap: 10px;",
            "  margin-top: 18px;",
            "}",
            ".bounded-meta--inline { margin-top: 12px; }",
            ".bounded-chip {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  min-height: 32px;",
            "  padding: 0 12px;",
            "  border-radius: 999px;",
            "  border: 1px solid color-mix(in srgb, var(--bounded-accent) 24%, white 10%);",
            "  background: color-mix(in srgb, var(--bounded-accent) 14%, transparent);",
            "  font-size: 12px;",
            "}",
            ".bounded-chip--muted {",
            "  border-color: var(--bounded-border);",
            "  background: rgba(255, 255, 255, 0.03);",
            "}",
            ".bounded-card, .bounded-stack { padding: 20px; }",
            ".bounded-card h2 { font-size: 1.05rem; }",
            ".bounded-copy { margin: 10px 0 0; }",
            ".bounded-rail { display: grid; gap: 16px; }",
            ".bounded-stack { display: grid; gap: 14px; }",
            ".bounded-stack--tight { gap: 10px; }",
            ".bounded-list {",
            "  margin: 0;",
            "  padding-left: 18px;",
            "  color: var(--bounded-muted);",
            "}",
            ".bounded-mini-card, .bounded-outline-item {",
            "  padding: 14px;",
            "  border-radius: 18px;",
            "  border: 1px solid rgba(255, 255, 255, 0.08);",
            "  background: rgba(255, 255, 255, 0.03);",
            "}",
            ".bounded-mini-card strong, .bounded-outline-item strong {",
            "  display: block;",
            "  margin-bottom: 6px;",
            "}",
            ".bounded-mini-card p, .bounded-outline-item p { margin: 0; }",
            "@media (max-width: 980px) {",
            "  .bounded-layout { grid-template-columns: 1fr; }",
            "}",
        ]
    )


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
        "renderer": {
            "kind": "next-app-scaffold",
            "entrypoints": [
                "next-app/app/page.tsx",
                "next-app/components/BoundedReferencePage.tsx",
                "next-app/components/reference-data.ts",
            ],
        },
        "note": "This scaffold is intentionally bounded. It is a starter for reconstruction when an exact reuse path is unavailable.",
    }
    html = _render_html(summary)
    css = _render_css(summary)
    tsx = _render_tsx(summary)
    prompt = _render_prompt(summary)
    app_model = _build_app_model(summary)
    app_data_ts = _render_reference_data_ts(app_model)
    app_component_tsx = _render_bounded_reference_page_tsx()
    app_page_tsx = _render_next_app_page_tsx()
    app_layout_tsx = _render_next_app_layout_tsx(summary)
    app_globals_css = _render_next_app_globals_css(summary)

    artifacts = {
        "layout-summary.json": summary,
        "app-model.json": app_model,
        "starter.html": html,
        "starter.css": css,
        "starter.tsx": tsx,
        "prompt.txt": prompt,
        "next-app/app/layout.tsx": app_layout_tsx,
        "next-app/app/page.tsx": app_page_tsx,
        "next-app/app/globals.css": app_globals_css,
        "next-app/components/BoundedReferencePage.tsx": app_component_tsx,
        "next-app/components/reference-data.ts": app_data_ts,
        "manifest.json": {
            "schema_version": SCAFFOLD_SCHEMA_VERSION,
            "coverage": summary["coverage"],
            "files": [
                "layout-summary.json",
                "app-model.json",
                "starter.html",
                "starter.css",
                "starter.tsx",
                "prompt.txt",
                "next-app/app/layout.tsx",
                "next-app/app/page.tsx",
                "next-app/app/globals.css",
                "next-app/components/BoundedReferencePage.tsx",
                "next-app/components/reference-data.ts",
            ],
            "app_entrypoints": summary["renderer"]["entrypoints"],
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
        target.parent.mkdir(parents=True, exist_ok=True)
        if filename.endswith(".json"):
            target.write_text(json.dumps(content, indent=2) + "\n")
        elif isinstance(content, str):
            target.write_text(content.rstrip() + "\n")
        else:
            target.write_text(str(content).rstrip() + "\n")
        persisted[filename] = str(target)

    return persisted
