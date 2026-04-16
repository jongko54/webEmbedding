"""Bounded rebuild scaffold generation for frame-blocked references."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


SCAFFOLD_SCHEMA_VERSION = "0.1.0"
NOISY_TAGS = {"script", "style", "meta", "link", "noscript"}
STYLE_SNAPSHOT_FIELDS = (
    "display",
    "position",
    "width",
    "height",
    "minWidth",
    "minHeight",
    "maxWidth",
    "maxHeight",
    "marginTop",
    "marginRight",
    "marginBottom",
    "marginLeft",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "overflow",
    "overflowX",
    "overflowY",
    "boxSizing",
    "zIndex",
    "transform",
    "transformOrigin",
    "color",
    "backgroundColor",
    "backgroundImage",
    "backgroundSize",
    "backgroundPosition",
    "backgroundRepeat",
    "backgroundClip",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "textAlign",
    "textTransform",
    "whiteSpace",
    "boxShadow",
    "borderRadius",
    "borderTopLeftRadius",
    "borderTopRightRadius",
    "borderBottomRightRadius",
    "borderBottomLeftRadius",
    "borderColor",
    "borderStyle",
    "borderWidth",
    "gap",
    "flexWrap",
    "alignContent",
    "justifyContent",
    "alignItems",
    "flexDirection",
    "opacity",
)


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


def _append_unique(items: list[str], value: str | None) -> None:
    if not value:
        return
    cleaned = " ".join(str(value).split())
    if cleaned and cleaned not in items:
        items.append(cleaned)


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
                    "width": styles.get("width"),
                    "height": styles.get("height"),
                    "minWidth": styles.get("minWidth"),
                    "minHeight": styles.get("minHeight"),
                    "maxWidth": styles.get("maxWidth"),
                    "maxHeight": styles.get("maxHeight"),
                    "marginTop": styles.get("marginTop"),
                    "marginRight": styles.get("marginRight"),
                    "marginBottom": styles.get("marginBottom"),
                    "marginLeft": styles.get("marginLeft"),
                    "paddingTop": styles.get("paddingTop"),
                    "paddingRight": styles.get("paddingRight"),
                    "paddingBottom": styles.get("paddingBottom"),
                    "paddingLeft": styles.get("paddingLeft"),
                    "overflow": styles.get("overflow"),
                    "overflowX": styles.get("overflowX"),
                    "overflowY": styles.get("overflowY"),
                    "boxSizing": styles.get("boxSizing"),
                    "zIndex": styles.get("zIndex"),
                    "transform": styles.get("transform"),
                    "transformOrigin": styles.get("transformOrigin"),
                    "color": styles.get("color"),
                    "backgroundColor": styles.get("backgroundColor"),
                    "backgroundImage": styles.get("backgroundImage"),
                    "backgroundSize": styles.get("backgroundSize"),
                    "backgroundPosition": styles.get("backgroundPosition"),
                    "backgroundRepeat": styles.get("backgroundRepeat"),
                    "backgroundClip": styles.get("backgroundClip"),
                    "fontFamily": styles.get("fontFamily"),
                    "fontSize": styles.get("fontSize"),
                    "fontWeight": styles.get("fontWeight"),
                    "lineHeight": styles.get("lineHeight"),
                    "letterSpacing": styles.get("letterSpacing"),
                    "textAlign": styles.get("textAlign"),
                    "textTransform": styles.get("textTransform"),
                    "whiteSpace": styles.get("whiteSpace"),
                    "boxShadow": styles.get("boxShadow"),
                    "borderRadius": styles.get("borderRadius"),
                    "borderTopLeftRadius": styles.get("borderTopLeftRadius"),
                    "borderTopRightRadius": styles.get("borderTopRightRadius"),
                    "borderBottomRightRadius": styles.get("borderBottomRightRadius"),
                    "borderBottomLeftRadius": styles.get("borderBottomLeftRadius"),
                    "borderColor": styles.get("borderColor"),
                    "borderStyle": styles.get("borderStyle"),
                    "borderWidth": styles.get("borderWidth"),
                    "gap": styles.get("gap"),
                    "flexWrap": styles.get("flexWrap"),
                    "alignContent": styles.get("alignContent"),
                    "justifyContent": styles.get("justifyContent"),
                    "alignItems": styles.get("alignItems"),
                    "flexDirection": styles.get("flexDirection"),
                    "opacity": styles.get("opacity"),
                },
                "styleSnapshot": _style_snapshot_from_styles(styles),
            }
        )
        if len(blocks) >= limit:
            break
    return blocks


def _style_snapshot_from_styles(styles: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(styles, dict):
        return {}
    snapshot: dict[str, str] = {}
    for field in STYLE_SNAPSHOT_FIELDS:
        value = styles.get(field)
        if value is None:
            continue
        cleaned = " ".join(str(value).split())
        if cleaned:
            snapshot[field] = cleaned
    return snapshot


def _style_snapshot_from_block(block: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(block, dict):
        return {}
    styles = block.get("styles", {}) if isinstance(block.get("styles", {}), dict) else {}
    snapshot = _style_snapshot_from_styles(styles)
    if snapshot:
        return snapshot
    raw_snapshot = block.get("styleSnapshot", {})
    return raw_snapshot if isinstance(raw_snapshot, dict) else {}


def _style_attr_from_snapshot(style_snapshot: dict[str, Any] | None) -> str:
    if not isinstance(style_snapshot, dict) or not style_snapshot:
        return ""
    css_map = {
        "display": "display",
        "position": "position",
        "width": "width",
        "height": "height",
        "minWidth": "min-width",
        "minHeight": "min-height",
        "maxWidth": "max-width",
        "maxHeight": "max-height",
        "marginTop": "margin-top",
        "marginRight": "margin-right",
        "marginBottom": "margin-bottom",
        "marginLeft": "margin-left",
        "paddingTop": "padding-top",
        "paddingRight": "padding-right",
        "paddingBottom": "padding-bottom",
        "paddingLeft": "padding-left",
        "overflow": "overflow",
        "overflowX": "overflow-x",
        "overflowY": "overflow-y",
        "boxSizing": "box-sizing",
        "zIndex": "z-index",
        "transform": "transform",
        "transformOrigin": "transform-origin",
        "color": "color",
        "backgroundColor": "background-color",
        "backgroundImage": "background-image",
        "backgroundSize": "background-size",
        "backgroundPosition": "background-position",
        "backgroundRepeat": "background-repeat",
        "backgroundClip": "background-clip",
        "fontFamily": "font-family",
        "fontSize": "font-size",
        "fontWeight": "font-weight",
        "lineHeight": "line-height",
        "letterSpacing": "letter-spacing",
        "textAlign": "text-align",
        "textTransform": "text-transform",
        "whiteSpace": "white-space",
        "boxShadow": "box-shadow",
        "borderRadius": "border-radius",
        "borderTopLeftRadius": "border-top-left-radius",
        "borderTopRightRadius": "border-top-right-radius",
        "borderBottomRightRadius": "border-bottom-right-radius",
        "borderBottomLeftRadius": "border-bottom-left-radius",
        "borderColor": "border-color",
        "borderStyle": "border-style",
        "borderWidth": "border-width",
        "gap": "gap",
        "flexWrap": "flex-wrap",
        "alignContent": "align-content",
        "justifyContent": "justify-content",
        "alignItems": "align-items",
        "flexDirection": "flex-direction",
        "opacity": "opacity",
    }
    parts: list[str] = []
    for field in STYLE_SNAPSHOT_FIELDS:
        value = style_snapshot.get(field)
        if value is None:
            continue
        cleaned = " ".join(str(value).split())
        if cleaned:
            parts.append(f"{css_map[field]}: {escape(cleaned)};")
    if not parts:
        return ""
    return ' style="' + " ".join(parts) + '"'


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
    line_heights: list[str | None] = []
    letter_spacings: list[str | None] = []
    for entry in style_entries:
        if not isinstance(entry, dict):
            continue
        styles = entry.get("styles", {}) if isinstance(entry.get("styles", {}), dict) else {}
        fonts.append(styles.get("fontFamily"))
        sizes.append(styles.get("fontSize"))
        weights.append(styles.get("fontWeight"))
        line_heights.append(styles.get("lineHeight"))
        letter_spacings.append(styles.get("letterSpacing"))
    return {
        "fonts": _collect_unique(fonts, limit=3),
        "sizes": _collect_unique(sizes, limit=4),
        "weights": _collect_unique(weights, limit=4),
        "line_heights": _collect_unique(line_heights, limit=4),
        "letter_spacings": _collect_unique(letter_spacings, limit=4),
    }


def _derive_style_tokens(style_entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    token_values: dict[str, list[str | None]] = {
        "display": [],
        "position": [],
        "font_families": [],
        "font_sizes": [],
        "font_weights": [],
        "line_heights": [],
        "letter_spacings": [],
        "text_aligns": [],
        "text_transforms": [],
        "white_spaces": [],
        "box_shadows": [],
        "border_radii": [],
        "border_colors": [],
        "border_styles": [],
        "border_widths": [],
        "gaps": [],
        "justify_contents": [],
        "align_items": [],
        "flex_directions": [],
    }
    for entry in style_entries:
        if not isinstance(entry, dict):
            continue
        styles = entry.get("styles", {}) if isinstance(entry.get("styles", {}), dict) else {}
        token_values["display"].append(styles.get("display"))
        token_values["position"].append(styles.get("position"))
        token_values["font_families"].append(styles.get("fontFamily"))
        token_values["font_sizes"].append(styles.get("fontSize"))
        token_values["font_weights"].append(styles.get("fontWeight"))
        token_values["line_heights"].append(styles.get("lineHeight"))
        token_values["letter_spacings"].append(styles.get("letterSpacing"))
        token_values["text_aligns"].append(styles.get("textAlign"))
        token_values["text_transforms"].append(styles.get("textTransform"))
        token_values["white_spaces"].append(styles.get("whiteSpace"))
        token_values["box_shadows"].append(styles.get("boxShadow"))
        token_values["border_radii"].append(styles.get("borderRadius"))
        token_values["border_colors"].append(styles.get("borderColor"))
        token_values["border_styles"].append(styles.get("borderStyle"))
        token_values["border_widths"].append(styles.get("borderWidth"))
        token_values["gaps"].append(styles.get("gap"))
        token_values["justify_contents"].append(styles.get("justifyContent"))
        token_values["align_items"].append(styles.get("alignItems"))
        token_values["flex_directions"].append(styles.get("flexDirection"))
    return {key: _collect_unique(values, limit=4) for key, values in token_values.items()}


def _collect_url_values(values: list[str | None], limit: int = 8) -> list[str]:
    seen: list[str] = []
    for value in values:
        if not value:
            continue
        cleaned = " ".join(str(value).split())
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen


def _build_asset_manifest(
    summary: dict[str, Any],
    asset_content: dict[str, Any],
    css_analysis: dict[str, Any],
    typography: dict[str, Any],
    style_tokens: dict[str, list[str]],
) -> dict[str, Any]:
    linked_stylesheets = css_analysis.get("linkedStylesheets", []) if isinstance(css_analysis, dict) else []
    stylesheet_urls = _collect_url_values(
        [
            *(asset_content.get("stylesheets", []) or []),
            *[
                item.get("href")
                for item in linked_stylesheets[:6]
                if isinstance(item, dict)
            ],
        ],
        limit=6,
    )
    font_families = _collect_unique(
        [
            *(typography.get("fonts") or []),
            *(style_tokens.get("font_families") or []),
            (css_analysis.get("bodyComputedStyle", {}) or {}).get("fontFamily") if isinstance(css_analysis, dict) else None,
            (css_analysis.get("rootComputedStyle", {}) or {}).get("fontFamily") if isinstance(css_analysis, dict) else None,
        ],
        limit=4,
    )
    return {
        "summary": {
            "images": len(asset_content.get("images", []) or []),
            "scripts": len(asset_content.get("scripts", []) or []),
            "stylesheets": len(asset_content.get("stylesheets", []) or []),
            "videos": len(asset_content.get("videos", []) or []),
            "audios": len(asset_content.get("audios", []) or []),
            "iframes": len(asset_content.get("iframes", []) or []),
        },
        "images": _collect_url_values(asset_content.get("images", []) or [], limit=12),
        "scripts": _collect_url_values(asset_content.get("scripts", []) or [], limit=12),
        "stylesheets": stylesheet_urls,
        "videos": _collect_url_values(asset_content.get("videos", []) or [], limit=8),
        "audios": _collect_url_values(asset_content.get("audios", []) or [], limit=8),
        "iframes": _collect_url_values(asset_content.get("iframes", []) or [], limit=8),
        "fonts": {
            "families": font_families,
            "bodyComputedStyle": (css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis, dict) else {}),
            "rootComputedStyle": (css_analysis.get("rootComputedStyle", {}) if isinstance(css_analysis, dict) else {}),
            "materializationStrategy": "stylesheet-imports-and-font-family-tokens",
        },
        "materialization": {
            "stylesheetImports": stylesheet_urls[:4],
            "fontFamilies": font_families[:4],
        },
        "styleTokens": style_tokens,
    }


def _render_next_app_fonts_css(summary: dict[str, Any]) -> str:
    asset_manifest = summary.get("assetManifest", {}) if isinstance(summary, dict) else {}
    fonts = asset_manifest.get("fonts", {}) if isinstance(asset_manifest, dict) else {}
    families = fonts.get("families", []) if isinstance(fonts, dict) else []
    stylesheet_imports = asset_manifest.get("materialization", {}).get("stylesheetImports", []) if isinstance(asset_manifest, dict) else []
    base_font = families[0] if families else ((summary.get("typography", {}) or {}).get("fonts") or ["Inter, system-ui, sans-serif"])[0]
    lines: list[str] = []
    for href in stylesheet_imports[:4]:
        lines.append(f'@import url("{href}");')
    if lines:
        lines.append("")
    lines.extend(
        [
            ":root {",
            f"  --bounded-font-sans: {base_font};",
            "}",
            "",
            "html, body {",
            "  font-family: var(--bounded-font-sans);",
            "}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


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


def _viewport_side(summary: dict[str, Any], key: str, fallback: int) -> int:
    viewport = summary.get("viewport", {}) if isinstance(summary, dict) else {}
    try:
        value = int(viewport.get(key) or fallback)
    except (TypeError, ValueError):
        value = fallback
    return max(value, 1)


def _block_rect_value(block: dict[str, Any], key: str) -> int:
    rect = block.get("rect", {}) if isinstance(block.get("rect", {}), dict) else {}
    try:
        return int(rect.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _infer_section_role(
    block: dict[str, Any],
    viewport_width: int,
    viewport_height: int,
    interaction_labels: set[str],
) -> str:
    tag = str(block.get("tag") or "div").lower()
    text = _clean_text(block.get("text"), 120)
    width = _block_rect_value(block, "width")
    height = _block_rect_value(block, "height")
    y = _block_rect_value(block, "y")

    if tag in {"header", "nav"}:
        return "masthead"
    if tag in {"a", "button", "input", "textarea"} or text.lower() in interaction_labels:
        return "action"
    if height >= int(viewport_height * 0.18) or width >= int(viewport_width * 0.76):
        return "hero"
    if y <= max(120, viewport_height // 8) and width >= int(viewport_width * 0.55):
        return "masthead"
    if width >= int(viewport_width * 0.5) and height <= max(140, viewport_height // 10):
        return "band"
    return "content"


def _renderer_confidence(summary: dict[str, Any]) -> str:
    signals = summary.get("signals", {}) if isinstance(summary, dict) else {}
    if all(bool(signals.get(label)) for label in ("dom_available", "styles_available", "interactions_available")):
        return "high"
    if sum(1 for value in signals.values() if value) >= 3:
        return "medium"
    return "low"


def _remaining_gaps(summary: dict[str, Any]) -> list[str]:
    gaps = [
        "Exact source reuse was unavailable, so this renderer is still bounded by capture artifacts.",
    ]
    signals = summary.get("signals", {}) if isinstance(summary, dict) else {}
    if not signals.get("breakpoint_variants_available"):
        gaps.append("Only a single viewport screenshot was captured, so breakpoint parity is not yet proven.")
    if not signals.get("dom_available"):
        gaps.append("DOM snapshot coverage is incomplete.")
    if not signals.get("styles_available"):
        gaps.append("Computed style coverage is incomplete.")
    if not signals.get("css_analysis_available"):
        gaps.append("Stylesheet and inline-style analysis is incomplete.")
    if not signals.get("interactions_available"):
        gaps.append("Interaction-state coverage is incomplete.")
    return gaps[:4]


def _generic_section_title(title: str) -> bool:
    lowered = title.strip().lower()
    return lowered.startswith("div block") or lowered.startswith("section ")


def _build_app_model(summary: dict[str, Any]) -> dict[str, Any]:
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    outline = summary.get("outline", []) if isinstance(summary, dict) else []
    interactions = (summary.get("interactions", {}) or {}).get("sample", [])
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    typography = summary.get("typography", {}) if isinstance(summary, dict) else {}
    viewport_width = _viewport_side(summary, "width", 1440)
    viewport_height = _viewport_side(summary, "height", 1200)
    interaction_labels = {
        _clean_text(entry.get("text"), 56).lower()
        for entry in interactions
        if isinstance(entry, dict) and _clean_text(entry.get("text"), 56)
    }

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
                "role": _infer_section_role(block, viewport_width, viewport_height, interaction_labels),
                "copy": _block_copy(block),
                "meta": f"{rect.get('width', 0)} x {rect.get('height', 0)} px",
                "details": detail_parts,
                "styleSnapshot": _style_snapshot_from_block(block),
                "rect": {
                    "x": rect.get("x", 0),
                    "y": rect.get("y", 0),
                    "width": rect.get("width", 0),
                    "height": rect.get("height", 0),
                },
            }
        )

    if not section_cards:
        section_cards.append(
            {
                "id": "section-1",
                "title": "Captured shell",
                "tag": "div",
                "role": "hero",
                "copy": "No rich DOM/style data was available, so this scaffold keeps a neutral shell and leaves the exact rebuild to downstream implementation.",
                "meta": "Fallback state",
                "details": [],
                "styleSnapshot": {},
                "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
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
        click_keys = entry.get("clickStateDeltaKeys", [])
        if click_keys:
            states.append(f"click: {', '.join(str(key) for key in click_keys)}")
        interaction_cards.append(
            {
                "id": f"interaction-{index + 1}",
                "label": _interaction_label(entry, index),
                "copy": _clean_text(entry.get("text"), 140) or "Interactive element sampled from runtime capture.",
                "tag": str(entry.get("tag") or "element"),
                "href": entry.get("href"),
                "states": states or ["interaction detected"],
                "styleSnapshot": _style_snapshot_from_styles(entry.get("baseStyles")),
                "rect": entry.get("rect"),
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
        f"platform: {summary.get('platform') or 'generic'}",
        f"blocks: {len(section_cards)}",
        f"assets: {(summary.get('assets', {}) or {}).get('image_count', 0)} images / {(summary.get('assets', {}) or {}).get('script_count', 0)} scripts",
        f"interactive states: {(summary.get('interactions', {}) or {}).get('count', 0)}",
    ]
    signal_bits = [
        label.replace("_", " ")
        for label, enabled in ((summary.get("signals", {}) or {}).items())
        if enabled
    ]
    adapter = summary.get("platform_adapter", {}) if isinstance(summary.get("platform_adapter", {}), dict) else {}
    def add_signal_bit(value: str | None) -> None:
        if not value:
            return
        cleaned = " ".join(str(value).split())
        if cleaned and cleaned not in signal_bits:
            signal_bits.append(cleaned)
    for signal in summary.get("source_signals", [])[:6] if isinstance(summary.get("source_signals", []), list) else []:
        add_signal_bit(f"source signal: {signal}")
    for note in adapter.get("notes", [])[:4] if isinstance(adapter.get("notes", []), list) else []:
        add_signal_bit(f"adapter note: {_clean_text(note, 96)}")
    if summary.get("candidate_count"):
        add_signal_bit(f"candidate urls: {summary.get('candidate_count')}")

    subtitle = (
        summary.get("description")
        or "Bounded rebuild scaffold derived from DOM, style, asset, and interaction capture. Use it as a practical app starter, not an exact reproduction claim."
    )
    hero_section = next((section for section in section_cards if section.get("role") == "hero"), section_cards[0])
    masthead_section = next((section for section in section_cards if section.get("role") == "masthead"), None)
    hero_title = hero_section.get("title") or str(summary.get("title") or "Captured reference")
    if _generic_section_title(str(hero_title)):
        hero_title = str(summary.get("title") or "Captured reference")
    hero_copy = hero_section.get("copy") or str(subtitle)
    if hero_copy == "Captured layout block derived from the source page structure.":
        hero_copy = str(subtitle)
    masthead_links = [
        {"label": card["label"], "href": card["href"]}
        for card in interaction_cards
        if card.get("href")
    ][:4]
    action_items = [
        {
            "label": card["label"],
            "href": card["href"],
            "states": card["states"],
            "styleSnapshot": card.get("styleSnapshot") or {},
        }
        for card in interaction_cards
        if card.get("href")
    ][:2]
    body_sections = [
        section
        for section in section_cards
        if section["id"] != hero_section["id"] and section.get("role") in {"content", "band"}
    ]
    if not body_sections:
        body_sections = [
            section
            for section in section_cards
            if section["id"] != hero_section["id"] and section.get("role") != "masthead"
        ] or (section_cards[1:] or section_cards[:1])
    rhythm = [
        {
            "id": section["id"],
            "role": section.get("role"),
            "size": section.get("meta"),
            "y": (section.get("rect") or {}).get("y"),
        }
        for section in section_cards
    ]

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
            "line_heights": typography.get("line_heights") or [],
            "letter_spacings": typography.get("letter_spacings") or [],
        },
        "styleTokens": summary.get("styleTokens") or {},
        "assetManifest": summary.get("assetManifest") or {},
        "sections": section_cards,
        "masthead": {
            "brand": str(summary.get("title") or "Captured reference"),
            "links": masthead_links,
            "styleSnapshot": (masthead_section or hero_section).get("styleSnapshot") if isinstance((masthead_section or hero_section), dict) else {},
        },
        "hero": {
            "eyebrow": "Role-inferred reconstruction",
            "title": hero_title,
            "copy": hero_copy,
            "meta": hero_section.get("meta"),
            "details": hero_section.get("details") or [],
            "actions": action_items,
            "styleSnapshot": hero_section.get("styleSnapshot") or {},
        },
        "bodySections": body_sections,
        "interactions": interaction_cards,
        "outline": outline_cards,
        "reconstruction": {
            "version": "reconstruction.v1",
            "strategy": "role-inferred-next-app",
            "confidence": _renderer_confidence(summary),
            "remainingGaps": _remaining_gaps(summary),
            "layoutRhythm": rhythm,
        },
        "platform": summary.get("platform") or "generic",
        "platformAdapter": adapter,
        "sourceSignals": summary.get("source_signals") or [],
        "candidateSample": summary.get("candidate_sample") or [],
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
            'import type { CSSProperties } from "react";',
            "",
            "type Props = {",
            "  data: BoundedReferenceData;",
            "};",
            "",
            "function styleFromSnapshot(snapshot?: Record<string, unknown> | null): CSSProperties | undefined {",
            "  if (!snapshot || typeof snapshot !== \"object\") {",
            "    return undefined;",
            "  }",
            "  const style: CSSProperties = {};",
            "  const set = (key: keyof CSSProperties, value: unknown) => {",
            "    if (typeof value === \"string\" && value.trim()) {",
            "      style[key] = value as CSSProperties[keyof CSSProperties];",
            "    }",
            "  };",
            "  set(\"display\", snapshot.display);",
            "  set(\"position\", snapshot.position);",
            "  set(\"width\", snapshot.width);",
            "  set(\"height\", snapshot.height);",
            "  set(\"minWidth\", snapshot.minWidth);",
            "  set(\"minHeight\", snapshot.minHeight);",
            "  set(\"maxWidth\", snapshot.maxWidth);",
            "  set(\"maxHeight\", snapshot.maxHeight);",
            "  set(\"marginTop\", snapshot.marginTop);",
            "  set(\"marginRight\", snapshot.marginRight);",
            "  set(\"marginBottom\", snapshot.marginBottom);",
            "  set(\"marginLeft\", snapshot.marginLeft);",
            "  set(\"paddingTop\", snapshot.paddingTop);",
            "  set(\"paddingRight\", snapshot.paddingRight);",
            "  set(\"paddingBottom\", snapshot.paddingBottom);",
            "  set(\"paddingLeft\", snapshot.paddingLeft);",
            "  set(\"overflow\", snapshot.overflow);",
            "  set(\"overflowX\", snapshot.overflowX);",
            "  set(\"overflowY\", snapshot.overflowY);",
            "  set(\"boxSizing\", snapshot.boxSizing);",
            "  set(\"zIndex\", snapshot.zIndex);",
            "  set(\"transform\", snapshot.transform);",
            "  set(\"transformOrigin\", snapshot.transformOrigin);",
            "  set(\"color\", snapshot.color);",
            "  set(\"backgroundColor\", snapshot.backgroundColor);",
            "  set(\"backgroundImage\", snapshot.backgroundImage);",
            "  set(\"backgroundSize\", snapshot.backgroundSize);",
            "  set(\"backgroundPosition\", snapshot.backgroundPosition);",
            "  set(\"backgroundRepeat\", snapshot.backgroundRepeat);",
            "  set(\"backgroundClip\", snapshot.backgroundClip);",
            "  set(\"fontFamily\", snapshot.fontFamily);",
            "  set(\"fontSize\", snapshot.fontSize);",
            "  set(\"fontWeight\", snapshot.fontWeight);",
            "  set(\"lineHeight\", snapshot.lineHeight);",
            "  set(\"letterSpacing\", snapshot.letterSpacing);",
            "  set(\"textAlign\", snapshot.textAlign);",
            "  set(\"textTransform\", snapshot.textTransform);",
            "  set(\"whiteSpace\", snapshot.whiteSpace);",
            "  set(\"boxShadow\", snapshot.boxShadow);",
            "  set(\"borderRadius\", snapshot.borderRadius);",
            "  set(\"borderTopLeftRadius\", snapshot.borderTopLeftRadius);",
            "  set(\"borderTopRightRadius\", snapshot.borderTopRightRadius);",
            "  set(\"borderBottomRightRadius\", snapshot.borderBottomRightRadius);",
            "  set(\"borderBottomLeftRadius\", snapshot.borderBottomLeftRadius);",
            "  set(\"borderColor\", snapshot.borderColor);",
            "  set(\"borderStyle\", snapshot.borderStyle);",
            "  set(\"borderWidth\", snapshot.borderWidth);",
            "  set(\"gap\", snapshot.gap);",
            "  set(\"flexWrap\", snapshot.flexWrap);",
            "  set(\"alignContent\", snapshot.alignContent);",
            "  set(\"justifyContent\", snapshot.justifyContent);",
            "  set(\"alignItems\", snapshot.alignItems);",
            "  set(\"flexDirection\", snapshot.flexDirection);",
            "  set(\"opacity\", snapshot.opacity);",
            "  return Object.keys(style).length ? style : undefined;",
            "}",
            "",
            "export function BoundedReferencePage({ data }: Props) {",
            "  const mastheadStyle = styleFromSnapshot(data.masthead.styleSnapshot);",
            "  const heroStyle = styleFromSnapshot(data.hero.styleSnapshot);",
            "  return (",
            '    <main className="bounded-shell">',
            '      <header className="bounded-masthead bounded-panel" style={mastheadStyle}>',
            '        <div className="bounded-brand-block">',
            '          <p className="bounded-eyebrow" style={mastheadStyle}>Captured reference</p>',
            '          <strong className="bounded-brand" style={mastheadStyle}>{data.masthead.brand}</strong>',
            "        </div>",
            '        <nav className="bounded-nav" aria-label="Captured navigation sample" style={mastheadStyle}>',
            "          {data.masthead.links.length ? (",
            "            data.masthead.links.map((link) => (",
            '              <a className="bounded-nav-link" href={link.href ?? "#"} key={`${link.label}-${link.href ?? "inline"}`} style={mastheadStyle}>',
            "                {link.label}",
            "              </a>",
            "            ))",
            "          ) : (",
            '            <span className="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>',
            "          )}",
            "        </nav>",
            "      </header>",
            "",
            '      <section className="bounded-hero bounded-panel" style={heroStyle}>',
            '        <p className="bounded-eyebrow" style={heroStyle}>{data.hero.eyebrow}</p>',
            '        <h1 style={heroStyle}>{data.hero.title}</h1>',
            '        <p className="bounded-lede" style={heroStyle}>{data.hero.copy}</p>',
            '        <div className="bounded-meta">',
            "          {data.metaBits.map((bit) => (",
            '            <span className="bounded-chip" key={bit} style={heroStyle}>',
            "              {bit}",
            "            </span>",
            "          ))}",
            "        </div>",
            '        <div className="bounded-hero-actions">',
            '          <span className="bounded-chip bounded-chip--muted" style={heroStyle}>{data.hero.meta}</span>',
            "          {data.hero.details.slice(0, 3).map((detail) => (",
            '            <span className="bounded-chip bounded-chip--muted" key={detail} style={heroStyle}>',
            "              {detail}",
            "            </span>",
            "          ))}",
            "          {data.hero.actions.map((action) => (",
            '            <a className="bounded-cta" href={action.href ?? "#"} key={`${action.label}-${action.href ?? "inline"}`} style={styleFromSnapshot(action.styleSnapshot)}>',
            "              {action.label}",
            "            </a>",
            "          ))}",
            "        </div>",
            "      </section>",
            "",
            '      <section className="bounded-layout">',
            '        <div className="bounded-main">',
            '          <section className="bounded-section-grid">',
            "            {data.bodySections.map((section) => (",
            '              <article className="bounded-card bounded-panel" data-role={section.role} key={section.id} style={styleFromSnapshot(section.styleSnapshot)}>',
            '                <div className="bounded-card-head">',
            '                  <p className="bounded-kicker" style={styleFromSnapshot(section.styleSnapshot)}>{section.role}</p>',
            '                  <span className="bounded-chip bounded-chip--muted" style={styleFromSnapshot(section.styleSnapshot)}>{section.tag}</span>',
            "                </div>",
            '                <h2 style={styleFromSnapshot(section.styleSnapshot)}>{section.title}</h2>',
            '                <p className="bounded-copy" style={styleFromSnapshot(section.styleSnapshot)}>{section.copy}</p>',
            '                <div className="bounded-meta bounded-meta--inline">',
            '                  <span className="bounded-chip" style={styleFromSnapshot(section.styleSnapshot)}>{section.meta}</span>',
            "                  {section.details.slice(0, 3).map((detail) => (",
            '                    <span className="bounded-chip bounded-chip--muted" key={detail} style={styleFromSnapshot(section.styleSnapshot)}>',
            "                      {detail}",
            "                    </span>",
            "                  ))}",
            "                </div>",
            "              </article>",
            "            ))}",
            "          </section>",
            "        </div>",
            "",
            '        <aside className="bounded-rail">',
            '          <section className="bounded-panel bounded-stack">',
            '            <p className="bounded-kicker">Renderer status</p>',
            '            <div className="bounded-status-row">',
            "              <strong>{data.reconstruction.strategy}</strong>",
            '              <span className="bounded-chip">{data.reconstruction.confidence}</span>',
            "            </div>",
            '            <ul className="bounded-list">',
            "              {data.reconstruction.remainingGaps.map((item) => (",
            "                <li key={item}>{item}</li>",
            "              ))}",
            "            </ul>",
            "          </section>",
            "",
            '          <section className="bounded-panel bounded-stack">',
            '            <p className="bounded-kicker">Signals</p>',
            '            <div className="bounded-meta bounded-meta--inline">',
            "              {data.signalBits.length ? (",
            "                data.signalBits.map((signal) => (",
            '                  <span className="bounded-chip bounded-chip--muted" key={signal}>',
            "                    {signal}",
            "                  </span>",
            "                ))",
            "              ) : (",
            '                <span className="bounded-chip bounded-chip--muted">No extra runtime signals were captured.</span>',
            "              )}",
            "            </div>",
            "          </section>",
            "",
            '          <section className="bounded-panel bounded-stack">',
            '            <p className="bounded-kicker">Interaction samples</p>',
            '            <div className="bounded-stack">',
            "              {data.interactions.length ? (",
            "                data.interactions.map((entry) => (",
            '                  <article className="bounded-mini-card" key={entry.id}>',
            '                    <strong>{entry.label}</strong>',
            '                    <p>{entry.copy}</p>',
            '                    <div className="bounded-meta bounded-meta--inline">',
            "                      {entry.states.map((state) => (",
            '                        <span className="bounded-chip bounded-chip--muted" key={state}>',
            "                          {state}",
            "                        </span>",
            "                      ))}",
            "                    </div>",
            "                  </article>",
            "                ))",
            "              ) : (",
            '                <article className="bounded-mini-card">',
            "                  <strong>No sampled interactions</strong>",
            "                  <p>Interaction data was not available in the capture bundle.</p>",
            "                </article>",
            "              )}",
            "            </div>",
            "          </section>",
            "",
            '          <section className="bounded-panel bounded-stack">',
            '            <p className="bounded-kicker">Layout rhythm</p>',
            '            <div className="bounded-stack bounded-stack--tight">',
            "              {data.reconstruction.layoutRhythm.slice(0, 6).map((item) => (",
            '                <article className="bounded-outline-item" key={item.id}>',
            '                  <strong>{item.role}</strong>',
            '                  <p>{item.size}</p>',
            '                  <span className="bounded-outline-meta">y: {item.y ?? 0}</span>',
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


def _render_bounded_reference_page_html(app_model: dict[str, Any]) -> str:
    masthead = app_model.get("masthead", {}) if isinstance(app_model, dict) else {}
    hero = app_model.get("hero", {}) if isinstance(app_model, dict) else {}
    reconstruction = app_model.get("reconstruction", {}) if isinstance(app_model, dict) else {}
    meta_bits = app_model.get("metaBits", []) if isinstance(app_model.get("metaBits", []), list) else []
    signal_bits = app_model.get("signalBits", []) if isinstance(app_model.get("signalBits", []), list) else []
    body_sections = app_model.get("bodySections", []) if isinstance(app_model.get("bodySections", []), list) else []
    interactions = app_model.get("interactions", []) if isinstance(app_model.get("interactions", []), list) else []
    layout_rhythm = reconstruction.get("layoutRhythm", []) if isinstance(reconstruction.get("layoutRhythm", []), list) else []
    masthead_style = _style_attr_from_snapshot(masthead.get("styleSnapshot"))
    hero_style = _style_attr_from_snapshot(hero.get("styleSnapshot"))

    nav_items = []
    for link in masthead.get("links", []) if isinstance(masthead.get("links", []), list) else []:
        if not isinstance(link, dict):
            continue
        label = escape(str(link.get("label") or "Captured link"))
        href = escape(str(link.get("href") or "#"))
        nav_items.append(f'              <a class="bounded-nav-link" href="{href}"{masthead_style}>{label}</a>')
    if not nav_items:
        nav_items.append('              <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>')

    hero_detail_bits = [f'          <span class="bounded-chip bounded-chip--muted"{hero_style}>{escape(str(hero.get("meta") or ""))}</span>'] if hero.get("meta") else []
    for detail in hero.get("details", [])[:3] if isinstance(hero.get("details", []), list) else []:
        hero_detail_bits.append(f'          <span class="bounded-chip bounded-chip--muted"{hero_style}>{escape(str(detail))}</span>')
    for action in hero.get("actions", []) if isinstance(hero.get("actions", []), list) else []:
        if not isinstance(action, dict):
            continue
        label = escape(str(action.get("label") or "Captured action"))
        href = escape(str(action.get("href") or "#"))
        hero_detail_bits.append(f'          <a class="bounded-cta" href="{href}"{_style_attr_from_snapshot(action.get("styleSnapshot"))}>{label}</a>')

    section_cards = []
    for section in body_sections:
        if not isinstance(section, dict):
            continue
        detail_bits = [f'                  <span class="bounded-chip">{escape(str(section.get("meta") or ""))}</span>'] if section.get("meta") else []
        for detail in section.get("details", [])[:3] if isinstance(section.get("details", []), list) else []:
            detail_bits.append(f'                  <span class="bounded-chip bounded-chip--muted">{escape(str(detail))}</span>')
        section_cards.append(
            "\n".join(
                [
                    f'              <article class="bounded-card bounded-panel" data-role="{escape(str(section.get("role") or "content"))}"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>',
                    '                <div class="bounded-card-head">',
                    f'                  <p class="bounded-kicker"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("role") or "content"))}</p>',
                    f'                  <span class="bounded-chip bounded-chip--muted"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("tag") or "div"))}</span>',
                    "                </div>",
                    f'                <h2{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("title") or "Captured section"))}</h2>',
                    f'                <p class="bounded-copy"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("copy") or ""))}</p>',
                    '                <div class="bounded-meta bounded-meta--inline">',
                    *detail_bits,
                    "                </div>",
                    "              </article>",
                ]
            )
        )
    if not section_cards:
        section_cards.append(
            "\n".join(
                [
                    '              <article class="bounded-card bounded-panel" data-role="content">',
                    '                <div class="bounded-card-head">',
                    '                  <p class="bounded-kicker">content</p>',
                    '                  <span class="bounded-chip bounded-chip--muted">fallback</span>',
                    "                </div>",
                    "                <h2>No sampled body sections</h2>",
                    '                <p class="bounded-copy">The capture bundle did not expose enough structure for a richer body layout.</p>',
                    "              </article>",
                ]
            )
        )

    interaction_cards = []
    for entry in interactions:
        if not isinstance(entry, dict):
            continue
        state_bits = []
        for state in entry.get("states", []) if isinstance(entry.get("states", []), list) else []:
            state_bits.append(f'                      <span class="bounded-chip bounded-chip--muted">{escape(str(state))}</span>')
        interaction_cards.append(
            "\n".join(
                [
                    '                <article class="bounded-mini-card">',
                    f'                  <strong>{escape(str(entry.get("label") or "Captured interaction"))}</strong>',
                    f'                  <p>{escape(str(entry.get("copy") or ""))}</p>',
                    '                  <div class="bounded-meta bounded-meta--inline">',
                    *(state_bits or ['                      <span class="bounded-chip bounded-chip--muted">interaction detected</span>']),
                    "                  </div>",
                    "                </article>",
                ]
            )
        )
    if not interaction_cards:
        interaction_cards.append(
            "\n".join(
                [
                    '                <article class="bounded-mini-card">',
                    "                  <strong>No sampled interactions</strong>",
                    "                  <p>Interaction data was not available in the capture bundle.</p>",
                    "                </article>",
                ]
            )
        )

    rhythm_cards = []
    for item in layout_rhythm[:6]:
        if not isinstance(item, dict):
            continue
        rhythm_cards.append(
            "\n".join(
                [
                    '                <article class="bounded-outline-item">',
                    f'                  <strong>{escape(str(item.get("role") or "section"))}</strong>',
                    f'                  <p>{escape(str(item.get("size") or ""))}</p>',
                    f'                  <span class="bounded-outline-meta">y: {escape(str(item.get("y") if item.get("y") is not None else 0))}</span>',
                    "                </article>",
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
            f'  <title>{escape(str(app_model.get("title") or "Captured reference"))}</title>',
            '  <link rel="stylesheet" href="./next-app/app/fonts.css" />',
            '  <link rel="stylesheet" href="./next-app/app/globals.css" />',
            "</head>",
            "<body>",
            '  <main class="bounded-shell">',
            f'    <header class="bounded-masthead bounded-panel"{masthead_style}>',
            '      <div class="bounded-brand-block">',
            f'        <p class="bounded-eyebrow"{masthead_style}>Captured reference</p>',
            f'        <strong class="bounded-brand"{masthead_style}>{escape(str(masthead.get("brand") or app_model.get("title") or "Captured reference"))}</strong>',
            "      </div>",
            f'      <nav class="bounded-nav" aria-label="Captured navigation sample"{masthead_style}>',
            *nav_items,
            "      </nav>",
            "    </header>",
            f'    <section class="bounded-hero bounded-panel"{hero_style}>',
            f'      <p class="bounded-eyebrow"{hero_style}>{escape(str(hero.get("eyebrow") or "Role-inferred reconstruction"))}</p>',
            f'      <h1{hero_style}>{escape(str(hero.get("title") or app_model.get("title") or "Captured reference"))}</h1>',
            f'      <p class="bounded-lede"{hero_style}>{escape(str(hero.get("copy") or app_model.get("subtitle") or ""))}</p>',
            '      <div class="bounded-meta">',
            *[f'        <span class="bounded-chip">{escape(str(bit))}</span>' for bit in meta_bits],
            "      </div>",
            '      <div class="bounded-hero-actions">',
            *hero_detail_bits,
            "      </div>",
            "    </section>",
            '    <section class="bounded-layout">',
            '      <div class="bounded-main">',
            '        <section class="bounded-section-grid">',
            *section_cards,
            "        </section>",
            "      </div>",
            '      <aside class="bounded-rail">',
            '        <section class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Renderer status</p>',
            '          <div class="bounded-status-row">',
            f'            <strong>{escape(str(reconstruction.get("strategy") or "role-inferred-next-app"))}</strong>',
            f'            <span class="bounded-chip">{escape(str(reconstruction.get("confidence") or "medium"))}</span>',
            "          </div>",
            '          <ul class="bounded-list">',
            *[f'            <li>{escape(str(item))}</li>' for item in reconstruction.get("remainingGaps", [])[:4] if isinstance(reconstruction.get("remainingGaps", []), list)],
            "          </ul>",
            "        </section>",
            '        <section class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Signals</p>',
            '          <div class="bounded-meta bounded-meta--inline">',
            *([f'            <span class="bounded-chip bounded-chip--muted">{escape(str(signal))}</span>' for signal in signal_bits] or ['            <span class="bounded-chip bounded-chip--muted">No extra runtime signals were captured.</span>']),
            "          </div>",
            "        </section>",
            '        <section class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Interaction samples</p>',
            '          <div class="bounded-stack">',
            *interaction_cards,
            "          </div>",
            "        </section>",
            '        <section class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Layout rhythm</p>',
            '          <div class="bounded-stack bounded-stack--tight">',
            *rhythm_cards,
            "          </div>",
            "        </section>",
            "      </aside>",
            "    </section>",
            "  </main>",
            "</body>",
            "</html>",
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
            'import "./fonts.css";',
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
    line_height = (typography.get("line_heights") or ["1.5"])[0]
    letter_spacing = (typography.get("letter_spacings") or ["-0.01em"])[0]
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
            f"  --bounded-body-line-height: {line_height};",
            f"  --bounded-heading-letter-spacing: {letter_spacing};",
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
            ".bounded-masthead {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  gap: 20px;",
            "  padding: 18px 22px;",
            "  margin-bottom: 16px;",
            "}",
            ".bounded-brand-block { min-width: 0; }",
            ".bounded-brand {",
            "  display: block;",
            "  font-size: 1rem;",
            "  letter-spacing: -0.02em;",
            "}",
            ".bounded-nav {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  justify-content: flex-end;",
            "  gap: 12px;",
            "}",
            ".bounded-nav-link {",
            "  color: inherit;",
            "  text-decoration: none;",
            "  font-size: 0.95rem;",
            "}",
            ".bounded-nav-link--muted { opacity: 0.72; }",
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
            "  letter-spacing: var(--bounded-heading-letter-spacing);",
            "}",
            ".bounded-hero h1 {",
            "  font-size: clamp(2.2rem, 4vw, 4.4rem);",
            "  line-height: 0.94;",
            "}",
            ".bounded-lede, .bounded-copy, .bounded-mini-card p, .bounded-outline-item p {",
            "  color: var(--bounded-muted);",
            "  line-height: var(--bounded-body-line-height);",
            "}",
            ".bounded-lede {",
            "  max-width: 62ch;",
            "  margin: 16px 0 0;",
            "}",
            ".bounded-hero-actions {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  gap: 10px;",
            "  margin-top: 18px;",
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
            ".bounded-cta {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  min-height: 38px;",
            "  padding: 0 16px;",
            "  border-radius: 999px;",
            "  text-decoration: none;",
            "  color: #09111d;",
            "  background: color-mix(in srgb, var(--bounded-accent) 84%, white 8%);",
            "  font-weight: 600;",
            "}",
            ".bounded-card, .bounded-stack { padding: 20px; }",
            ".bounded-card h2 { font-size: 1.05rem; }",
            ".bounded-copy { margin: 10px 0 0; }",
            ".bounded-card-head {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  gap: 12px;",
            "}",
            ".bounded-card[data-role=\"hero\"], .bounded-card[data-role=\"band\"] {",
            "  grid-column: 1 / -1;",
            "}",
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
            ".bounded-status-row {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  gap: 12px;",
            "}",
            ".bounded-outline-meta {",
            "  display: inline-block;",
            "  margin-top: 8px;",
            "  color: var(--bounded-muted);",
            "  font-size: 12px;",
            "}",
            "@media (max-width: 980px) {",
            "  .bounded-masthead {",
            "    flex-direction: column;",
            "    align-items: flex-start;",
            "  }",
            "  .bounded-nav { justify-content: flex-start; }",
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
    css_analysis_capture = captures.get("cssAnalysis", {}) if isinstance(captures, dict) else {}

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
                "clickStateDeltaKeys": sorted((((entry.get("clickState") or {}).get("stateDelta")) or {}).keys())
                if isinstance(((entry.get("clickState") or {}).get("stateDelta")), dict)
                else [],
            }
        )

    frame_policy = static.get("frame_policy", {}) if isinstance(static.get("frame_policy", {}), dict) else {}
    meta = static.get("meta", {}) if isinstance(static.get("meta", {}), dict) else {}
    breakpoint_summary = capture_bundle.get("breakpoints", {}) if isinstance(capture_bundle, dict) else {}
    breakpoint_variants = breakpoint_summary.get("variants", []) if isinstance(breakpoint_summary, dict) else []
    css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
    typography = _derive_typography(style_entries)
    style_tokens = _derive_style_tokens(style_entries)

    summary = {
        "schema_version": SCAFFOLD_SCHEMA_VERSION,
        "coverage": "bounded-rebuild-scaffold",
        "source_url": capture_bundle.get("url"),
        "final_url": static.get("final_url"),
        "title": static.get("title") or "Captured reference",
        "description": meta.get("description"),
        "policy_mode": policy.get("mode"),
        "frame_policy": frame_policy,
        "platform": static.get("platform") or "generic",
        "platform_adapter": static.get("platform_adapter") or {},
        "source_signals": static.get("source_signals") or [],
        "candidate_count": len(static.get("candidate_urls") or []),
        "candidate_sample": (static.get("candidate_urls") or [])[:6],
        "viewport": {
            "width": session_request.get("viewport_width"),
            "height": session_request.get("viewport_height"),
        },
        "signals": {
            "dom_available": bool(dom_capture.get("available")),
            "styles_available": bool(styles_capture.get("available")),
            "css_analysis_available": bool(css_analysis_capture.get("available")),
            "assets_available": bool(assets_capture.get("available")),
            "interactions_available": bool(interactions_capture.get("available")),
            "runtime_available": bool(runtime.get("available")),
            "breakpoint_variants_available": bool(breakpoint_variants),
        },
        "breakpoints": {
            "requested_profiles": breakpoint_summary.get("requested_profiles") if isinstance(breakpoint_summary, dict) else [],
            "captured_count": breakpoint_summary.get("captured_count") if isinstance(breakpoint_summary, dict) else 0,
            "variant_count": len(breakpoint_variants) if isinstance(breakpoint_variants, list) else 0,
        },
        "outline": outline[:12],
        "blocks": blocks,
        "palette": _derive_palette(style_entries),
        "typography": typography,
        "styleTokens": style_tokens,
        "assets": {
            "image_count": image_count,
            "script_count": script_count,
            "iframe_count": iframe_count,
        },
        "cssAnalysis": {
            "stylesheet_count": css_analysis.get("stylesheetCount", 0) if isinstance(css_analysis, dict) else 0,
            "accessible_stylesheet_count": css_analysis.get("accessibleStylesheetCount", 0) if isinstance(css_analysis, dict) else 0,
            "inline_style_tag_count": css_analysis.get("inlineStyleTagCount", 0) if isinstance(css_analysis, dict) else 0,
            "style_attribute_count": css_analysis.get("styleAttributeCount", 0) if isinstance(css_analysis, dict) else 0,
            "stylesheet_sample": [
                {
                    "href": item.get("href"),
                    "ownerTag": item.get("ownerTag"),
                    "ruleCount": item.get("ruleCount"),
                    "restricted": item.get("crossOriginRestricted"),
                }
                for item in (css_analysis.get("linkedStylesheets", [])[:4] if isinstance(css_analysis.get("linkedStylesheets", []), list) else [])
                if isinstance(item, dict)
            ],
        },
        "interactions": {
            "count": len(interaction_entries) if isinstance(interaction_entries, list) else 0,
            "sample": interaction_sample,
        },
        "renderer": {
            "kind": "role-inferred-next-app",
            "strategy": "capture-bundle-to-sectioned-app",
            "entrypoints": [
                "next-app/app/page.tsx",
                "next-app/components/BoundedReferencePage.tsx",
                "next-app/components/reference-data.ts",
            ],
        },
        "note": "This scaffold is intentionally bounded. It is a starter for reconstruction when an exact reuse path is unavailable.",
    }
    asset_manifest = _build_asset_manifest(summary, asset_content, css_analysis, typography, style_tokens)
    summary["assetManifest"] = asset_manifest
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
    app_fonts_css = _render_next_app_fonts_css(summary)
    app_preview_html = _render_bounded_reference_page_html(app_model)

    artifacts = {
        "layout-summary.json": summary,
        "app-model.json": app_model,
        "starter.html": html,
        "starter.css": css,
        "starter.tsx": tsx,
        "prompt.txt": prompt,
        "next-app/app/layout.tsx": app_layout_tsx,
        "next-app/app/page.tsx": app_page_tsx,
        "next-app/app/fonts.css": app_fonts_css,
        "next-app/app/globals.css": app_globals_css,
        "app-preview.html": app_preview_html,
        "next-app/components/BoundedReferencePage.tsx": app_component_tsx,
        "next-app/components/reference-data.ts": app_data_ts,
        "assets/asset-manifest.json": asset_manifest,
        "assets/font-manifest.json": asset_manifest.get("fonts", {}),
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
                "next-app/app/fonts.css",
                "next-app/app/globals.css",
                "app-preview.html",
                "next-app/components/BoundedReferencePage.tsx",
                "next-app/components/reference-data.ts",
                "assets/asset-manifest.json",
                "assets/font-manifest.json",
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
