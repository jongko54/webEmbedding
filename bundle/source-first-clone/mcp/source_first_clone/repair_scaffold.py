"""Bounded auto-repair pass for scaffold artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .rebuild_scaffold import (
    _build_asset_manifest,
    _build_runtime_materialization,
    _derive_style_tokens,
    _derive_typography,
    _is_google_submit_label,
    _render_bounded_reference_page_tsx,
    _render_bounded_reference_page_html,
    _render_next_app_fonts_css,
    _render_next_app_layout_tsx,
    _render_reference_data_ts,
)


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    payload = json.loads(candidate.read_text())
    return payload if isinstance(payload, dict) else {}


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.exists():
        return ""
    return candidate.read_text()


def _append_unique(items: list[str], value: str | None) -> None:
    if not value:
        return
    cleaned = " ".join(str(value).split())
    if cleaned and cleaned not in items:
        items.append(cleaned)


def _clean_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _rgb_channels(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.search(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", str(value))
    if not match:
        return None
    return tuple(int(match.group(index)) for index in range(1, 4))


def _color_luma(value: str | None) -> float | None:
    channels = _rgb_channels(value)
    if not channels:
        return None
    red, green, blue = channels
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def _unique_link_entries(capture_bundle: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    interaction_entries = captures.get("interactions", {}) if isinstance(captures.get("interactions", {}), dict) else {}
    content = interaction_entries.get("content", []) if isinstance(interaction_entries, dict) else []
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in content if isinstance(content, list) else []:
        if not isinstance(entry, dict):
            continue
        href = str(entry.get("href") or "").strip()
        label = _clean_text(entry.get("text") or entry.get("ariaLabel"), 56)
        if not href or not label:
            continue
        key = (label, href)
        if key in seen:
            continue
        seen.add(key)
        unique.append({"label": label, "href": href})
        if len(unique) >= limit:
            break
    return unique


def _outline_text_sample(summary: dict[str, Any], limit: int = 4) -> list[str]:
    outline = summary.get("outline", []) if isinstance(summary, dict) else []
    sample: list[str] = []
    for entry in outline if isinstance(outline, list) else []:
        if not isinstance(entry, dict):
            continue
        text = _clean_text(entry.get("text"), 96)
        if not text:
            continue
        _append_unique(sample, text)
        if len(sample) >= limit:
            break
    return sample[:limit]


def _section_style_baseline(style: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(style, dict):
        return {}
    fields = (
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
    return {field: style.get(field) for field in fields if style.get(field) is not None}


def _is_transparent_color(value: Any) -> bool:
    if value is None:
        return True
    lowered = str(value).strip().lower()
    return lowered in {"", "transparent", "rgba(0, 0, 0, 0)", "rgba(0,0,0,0)", "none"}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _choose_section_card(
    section_cards: list[dict[str, Any]],
    roles: set[str],
    tags: set[str] | None = None,
) -> dict[str, Any] | None:
    tags = tags or set()
    for section in section_cards:
        if not isinstance(section, dict):
            continue
        if str(section.get("role") or "").lower() in roles and (
            not tags or str(section.get("tag") or "").lower() in tags
        ):
            return section
    for section in section_cards:
        if not isinstance(section, dict):
            continue
        if str(section.get("role") or "").lower() in roles:
            return section
    for section in section_cards:
        if not isinstance(section, dict):
            continue
        if not tags or str(section.get("tag") or "").lower() in tags:
            return section
    return section_cards[0] if section_cards else None


def _repair_section_style_snapshot(
    section: dict[str, Any],
    palette: dict[str, Any],
    typography: dict[str, Any],
    style_tokens: dict[str, Any],
) -> dict[str, Any]:
    role = str(section.get("role") or "content").lower()
    tag = str(section.get("tag") or "div").lower()
    captured_style = _section_style_baseline(section.get("style", {}))
    resolved_style = dict(captured_style)
    mutations: list[str] = []

    def apply(key: str, value: Any, reason: str, replace_transparent: bool = False) -> None:
        if value in (None, ""):
            return
        current = resolved_style.get(key)
        if current not in (None, "") and not (replace_transparent and key == "backgroundColor" and _is_transparent_color(current)):
            return
        resolved_style[key] = value
        _append_unique(mutations, reason)

    text_color = palette.get("text")
    surface_color = _first_non_empty(palette.get("surface"), palette.get("surfaceAlt"))
    alt_surface_color = _first_non_empty(palette.get("surfaceAlt"), palette.get("surface"))
    font_family = _first_non_empty(
        (typography.get("fonts") or [None])[0] if isinstance(typography.get("fonts", []), list) else None,
        (style_tokens.get("font_families") or [None])[0] if isinstance(style_tokens.get("font_families", []), list) else None,
    )
    font_size = _first_non_empty(
        (typography.get("sizes") or [None])[0] if isinstance(typography.get("sizes", []), list) else None,
        (style_tokens.get("font_sizes") or [None])[0] if isinstance(style_tokens.get("font_sizes", []), list) else None,
    )
    line_height = _first_non_empty(
        (typography.get("line_heights") or [None])[0] if isinstance(typography.get("line_heights", []), list) else None,
        (style_tokens.get("line_heights") or [None])[0] if isinstance(style_tokens.get("line_heights", []), list) else None,
    )
    letter_spacing = _first_non_empty(
        (typography.get("letter_spacings") or [None])[0] if isinstance(typography.get("letter_spacings", []), list) else None,
        (style_tokens.get("letter_spacings") or [None])[0] if isinstance(style_tokens.get("letter_spacings", []), list) else None,
    )
    font_weight = _first_non_empty(
        (typography.get("weights") or [None])[0] if isinstance(typography.get("weights", []), list) else None,
        (style_tokens.get("font_weights") or [None])[0] if isinstance(style_tokens.get("font_weights", []), list) else None,
    )

    if role == "masthead" or tag in {"nav", "header"}:
        apply("backgroundColor", alt_surface_color, "Filled masthead/nav background from repaired surface tokens.", replace_transparent=True)
    else:
        apply("backgroundColor", surface_color, "Filled section background from repaired surface tokens.", replace_transparent=True)
    apply("color", text_color, "Filled section text color from repaired palette tokens.")
    apply("fontFamily", font_family, "Filled section font family from repaired typography tokens.")
    apply("fontSize", font_size, "Filled section font size from repaired typography tokens.")
    apply("lineHeight", line_height, "Filled section line height from repaired typography tokens.")
    apply("letterSpacing", letter_spacing, "Filled section letter spacing from repaired typography tokens.")
    apply("fontWeight", font_weight, "Filled section font weight from repaired typography tokens.")

    return {
        "capturedStyle": captured_style,
        "resolvedStyle": resolved_style,
        "mutations": mutations[:6],
    }


def _build_section_style_snapshots(
    summary: dict[str, Any],
    palette: dict[str, Any],
    typography: dict[str, Any],
    style_tokens: dict[str, Any],
) -> dict[str, Any]:
    sections = summary.get("sections", []) if isinstance(summary, dict) else []
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    paired_sections: list[dict[str, Any]] = []
    section_by_id: dict[str, dict[str, Any]] = {}
    for index, section in enumerate(sections if isinstance(sections, list) else []):
        if not isinstance(section, dict):
            continue
        block = blocks[index] if isinstance(blocks, list) and index < len(blocks) and isinstance(blocks[index], dict) else {}
        enriched = dict(section)
        enriched["style"] = _section_style_baseline(block.get("styles", {}))
        style_snapshot = _repair_section_style_snapshot(enriched, palette, typography, style_tokens)
        enriched["styleSnapshot"] = style_snapshot
        paired_sections.append(enriched)
        section_id = str(enriched.get("id") or "")
        if section_id:
            section_by_id[section_id] = style_snapshot

    hero_section = _choose_section_card(paired_sections, {"hero"})
    nav_section = _choose_section_card(paired_sections, {"masthead"}, {"nav", "header"})
    body_sections = [
        section
        for section in paired_sections
        if str(section.get("role") or "").lower() in {"content", "band"} and section.get("id") != (hero_section or {}).get("id")
    ]
    body_primary = body_sections[0] if body_sections else _choose_section_card(paired_sections, {"content", "band"})

    return {
        "sections": paired_sections,
        "hero": hero_section.get("styleSnapshot", {}) if isinstance(hero_section, dict) else {},
        "nav": nav_section.get("styleSnapshot", {}) if isinstance(nav_section, dict) else {},
        "body": body_primary.get("styleSnapshot", {}) if isinstance(body_primary, dict) else {},
        "bodySections": [
            {
                **section,
                "styleSnapshot": section.get("styleSnapshot", {}),
            }
            for section in body_sections
        ],
        "byId": section_by_id,
    }


def _section_style_css_lines(selector: str, snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    style = snapshot.get("resolvedStyle", {}) if isinstance(snapshot.get("resolvedStyle", {}), dict) else {}
    prop_map = (
        ("backgroundColor", "background-color"),
        ("color", "color"),
        ("width", "width"),
        ("height", "height"),
        ("minWidth", "min-width"),
        ("minHeight", "min-height"),
        ("maxWidth", "max-width"),
        ("maxHeight", "max-height"),
        ("marginTop", "margin-top"),
        ("marginRight", "margin-right"),
        ("marginBottom", "margin-bottom"),
        ("marginLeft", "margin-left"),
        ("paddingTop", "padding-top"),
        ("paddingRight", "padding-right"),
        ("paddingBottom", "padding-bottom"),
        ("paddingLeft", "padding-left"),
        ("overflow", "overflow"),
        ("overflowX", "overflow-x"),
        ("overflowY", "overflow-y"),
        ("boxSizing", "box-sizing"),
        ("zIndex", "z-index"),
        ("transform", "transform"),
        ("transformOrigin", "transform-origin"),
        ("backgroundImage", "background-image"),
        ("backgroundSize", "background-size"),
        ("backgroundPosition", "background-position"),
        ("backgroundRepeat", "background-repeat"),
        ("backgroundClip", "background-clip"),
        ("fontFamily", "font-family"),
        ("fontSize", "font-size"),
        ("fontWeight", "font-weight"),
        ("lineHeight", "line-height"),
        ("letterSpacing", "letter-spacing"),
        ("textAlign", "text-align"),
        ("textTransform", "text-transform"),
        ("whiteSpace", "white-space"),
        ("boxShadow", "box-shadow"),
        ("borderRadius", "border-radius"),
        ("borderTopLeftRadius", "border-top-left-radius"),
        ("borderTopRightRadius", "border-top-right-radius"),
        ("borderBottomRightRadius", "border-bottom-right-radius"),
        ("borderBottomLeftRadius", "border-bottom-left-radius"),
        ("borderColor", "border-color"),
        ("borderStyle", "border-style"),
        ("borderWidth", "border-width"),
        ("gap", "gap"),
        ("flexWrap", "flex-wrap"),
        ("alignContent", "align-content"),
        ("justifyContent", "justify-content"),
        ("alignItems", "align-items"),
        ("flexDirection", "flex-direction"),
        ("opacity", "opacity"),
        ("display", "display"),
        ("position", "position"),
    )
    lines = [f"{selector} {{"]
    emitted = False
    for key, css_name in prop_map:
        value = style.get(key)
        if value in (None, ""):
            continue
        if key == "backgroundColor" and _is_transparent_color(value):
            continue
        lines.append(f"  {css_name}: {value};")
        emitted = True
    lines.append("}")
    return lines if emitted else []


def _compact_body_sections(
    summary: dict[str, Any],
    links: list[dict[str, Any]],
    trace_bits: list[str],
    section_style_snapshots: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    title = _clean_text(summary.get("title"), 56) or "Captured reference"
    assets = summary.get("assets", {}) if isinstance(summary.get("assets", {}), dict) else {}
    section_style_snapshots = section_style_snapshots or {}
    sections: list[dict[str, Any]] = []
    if links:
        nav_snapshot = section_style_snapshots.get("nav", {}) if isinstance(section_style_snapshots.get("nav", {}), dict) else {}
        sections.append(
            {
                "id": "repair-nav",
                "title": "Primary actions",
                "tag": "nav",
                "role": "band",
                "copy": " · ".join(link["label"] for link in links[:4]),
                "meta": f"{len(links)} reusable links",
                "details": [link["href"] for link in links[:2]],
                "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
                "styleSnapshot": nav_snapshot,
            }
        )
    body_snapshot = section_style_snapshots.get("body", {}) if isinstance(section_style_snapshots.get("body", {}), dict) else {}
    sections.append(
        {
            "id": "repair-structure",
            "title": f"{title} footprint",
            "tag": "section",
            "role": "content",
            "copy": "Bounded structure reconstructed from sampled DOM, CSS, and interaction signals.",
            "meta": f"{assets.get('image_count', 0)} images / {assets.get('script_count', 0)} scripts",
            "details": trace_bits[:3],
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            "styleSnapshot": body_snapshot,
        }
    )
    return sections[:2]


def _state_bits_from_snapshot(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    semantic = snapshot.get("semanticState", {}) if isinstance(snapshot.get("semanticState", {}), dict) else snapshot
    bits: list[str] = []
    for key in ("ariaExpanded", "ariaPressed", "ariaSelected", "checked", "selected", "open", "hidden", "disabled"):
        value = semantic.get(key)
        if value is not None:
            _append_unique(bits, f"{key}:{value}")
    for key in ("value", "placeholder", "href", "role", "type", "activeElementTag", "scrollY"):
        value = semantic.get(key)
        if value:
            _append_unique(bits, f"{key}:{_clean_text(value, 48)}")
    dataset_keys = semantic.get("datasetKeys", []) if isinstance(semantic.get("datasetKeys", []), list) else []
    if dataset_keys:
        _append_unique(bits, f"dataset:{', '.join(str(item) for item in dataset_keys[:4])}")
    return bits[:6]


def _looks_like_flat_reference(summary: dict[str, Any]) -> bool:
    blocks = summary.get("blocks", []) if isinstance(summary, dict) else []
    if not isinstance(blocks, list) or not blocks:
        return False
    inspected = 0
    flat_votes = 0
    for block in blocks[:10]:
        if not isinstance(block, dict):
            continue
        styles = block.get("styles", {}) if isinstance(block.get("styles", {}), dict) else {}
        inspected += 1
        background = str(styles.get("backgroundColor") or "")
        radius = str(styles.get("borderRadius") or "")
        font_size = str(styles.get("fontSize") or "")
        if background in {"rgba(0, 0, 0, 0)", "transparent", "none"} and radius in {"0px", "0"}:
            flat_votes += 1
        if font_size == "14px":
            flat_votes += 1
    return inspected > 0 and flat_votes >= inspected


def _footer_surface_summary(app_model: dict[str, Any]) -> dict[str, Any]:
    footer = app_model.get("footer", {}) if isinstance(app_model, dict) else {}
    footer = footer if isinstance(footer, dict) else {}
    left_links = footer.get("leftLinks", []) if isinstance(footer.get("leftLinks", []), list) else []
    right_links = footer.get("rightLinks", []) if isinstance(footer.get("rightLinks", []), list) else []
    controls = footer.get("controls", []) if isinstance(footer.get("controls", []), list) else []
    return {
        "present": bool(footer.get("styleSnapshot") or left_links or right_links or controls),
        "leftLinks": len(left_links),
        "rightLinks": len(right_links),
        "controls": len(controls),
    }


def _centered_focus_surface_summary(app_model: dict[str, Any]) -> dict[str, Any]:
    hero = app_model.get("hero", {}) if isinstance(app_model, dict) else {}
    hero = hero if isinstance(hero, dict) else {}
    layout_mode = str(app_model.get("layoutMode") or "")
    focus_input = hero.get("focusInput", {}) if isinstance(hero.get("focusInput", {}), dict) else {}
    focus_shell_style = hero.get("focusShellStyle", {}) if isinstance(hero.get("focusShellStyle", {}), dict) else {}
    focus_auxiliary = hero.get("focusAuxiliary", []) if isinstance(hero.get("focusAuxiliary", []), list) else []
    has_focus_surface = bool(layout_mode == "centered-focus" or focus_input or focus_shell_style or focus_auxiliary)
    return {
        "present": has_focus_surface,
        "layoutMode": layout_mode,
        "focusInput": bool(focus_input),
        "focusShell": bool(focus_shell_style),
        "focusAuxiliary": len(focus_auxiliary),
    }


def _breakpoint_style_baseline(style: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(style, dict):
        return {}
    return {
        "backgroundColor": style.get("backgroundColor"),
        "color": style.get("color"),
        "fontFamily": style.get("fontFamily"),
        "fontSize": style.get("fontSize"),
        "lineHeight": style.get("lineHeight"),
        "letterSpacing": style.get("letterSpacing"),
    }


def _load_breakpoint_contexts(capture_bundle: dict[str, Any], repair_plan: dict[str, Any]) -> list[dict[str, Any]]:
    breakpoints = capture_bundle.get("breakpoints", {}) if isinstance(capture_bundle, dict) else {}
    variants = breakpoints.get("variants", []) if isinstance(breakpoints, dict) else []
    breakpoint_focus_entries = repair_plan.get("breakpoint_focus", []) if isinstance(repair_plan, dict) else []
    focus_by_name = {
        str(item.get("name") or "").strip().lower(): item
        for item in breakpoint_focus_entries
        if isinstance(item, dict) and item.get("name")
    }
    contexts: list[dict[str, Any]] = []
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict) or not variant.get("available"):
            continue
        name = str(variant.get("name") or "").strip().lower()
        if not name:
            continue
        manifest_path = variant.get("capture_manifest")
        variant_bundle = _read_json(manifest_path) if manifest_path else {}
        if not variant_bundle:
            continue
        runtime = variant_bundle.get("runtime", {}) if isinstance(variant_bundle, dict) else {}
        captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
        styles_capture = captures.get("styles", {}) if isinstance(captures.get("styles", {}), dict) else {}
        style_entries = styles_capture.get("content", []) if isinstance(styles_capture, dict) else []
        css_analysis_capture = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis", {}), dict) else {}
        css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
        body_style = css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis.get("bodyComputedStyle", {}), dict) else {}
        root_style = css_analysis.get("rootComputedStyle", {}) if isinstance(css_analysis.get("rootComputedStyle", {}), dict) else {}
        focus_entry = focus_by_name.get(name, {})
        focus_text = str(focus_entry.get("focus") or "")
        contexts.append(
            {
                "name": name,
                "viewport": variant.get("viewport") or {},
                "score": focus_entry.get("score"),
                "focus": focus_entry.get("focus"),
                "layoutSensitive": any(
                    token in focus_text.lower()
                    for token in ("screenshot", "layout", "spacing", "hierarchy")
                ),
                "bodyStyle": _breakpoint_style_baseline(body_style),
                "rootStyle": _breakpoint_style_baseline(root_style),
                "typography": _derive_typography(style_entries if isinstance(style_entries, list) else []),
                "styleTokens": _derive_style_tokens(style_entries if isinstance(style_entries, list) else []),
            }
        )
    return contexts


def _render_repaired_bounded_reference_page_component() -> str:
    return "\n".join(
        [
            'import type { BoundedReferenceData } from "./reference-data";',
            "",
            "type Props = {",
            "  data: BoundedReferenceData;",
            "};",
            "",
            "export function BoundedReferencePage({ data }: Props) {",
            '  const variant = data.presentation?.variant ?? "default";',
            '  const compact = variant === "compact-center-stage";',
            "  const bodySections = compact ? data.bodySections.slice(0, 2) : data.bodySections;",
            "  const interactionEntries = compact ? data.interactions.slice(0, 3) : data.interactions;",
            "  const heroBits = compact ? data.metaBits.slice(0, 2) : data.metaBits;",
            "  return (",
            '    <main className={`bounded-shell ${compact ? "bounded-shell--compact" : ""}`.trim()}>',
            '      <header className={`bounded-masthead bounded-panel ${compact ? "bounded-masthead--compact" : ""}`.trim()}>',
            '        <div className="bounded-brand-block">',
            '          <p className="bounded-eyebrow">{compact ? "Reference shell" : "Captured reference"}</p>',
            '          <strong className="bounded-brand">{data.masthead.brand}</strong>',
            "        </div>",
            '        <nav className="bounded-nav" aria-label="Captured navigation sample">',
            "          {data.masthead.links.length ? (",
            "            data.masthead.links.map((link) => (",
            '              <a className="bounded-nav-link" href={link.href ?? "#"} key={`${link.label}-${link.href ?? "inline"}`}>',
            "                {link.label}",
            "              </a>",
            "            ))",
            "          ) : (",
            '            <span className="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>',
            "          )}",
            "        </nav>",
            "      </header>",
            "",
            '      <section className={`bounded-hero bounded-panel ${compact ? "bounded-hero--compact" : ""}`.trim()}>',
            "        {compact ? <div className=\"bounded-hero-orb\" aria-hidden=\"true\" /> : null}",
            '        <p className="bounded-eyebrow">{data.hero.eyebrow}</p>',
            "        <h1>{data.hero.title}</h1>",
            '        <p className="bounded-lede">{data.hero.copy}</p>',
            '        <div className="bounded-meta">',
            "          {heroBits.map((bit) => (",
            '            <span className="bounded-chip" key={bit}>',
            "              {bit}",
            "            </span>",
            "          ))}",
            "        </div>",
            '        <div className={`bounded-hero-actions ${compact ? "bounded-hero-actions--compact" : ""}`.trim()}>',
            '          <span className="bounded-chip bounded-chip--muted">{data.hero.meta}</span>',
            "          {data.hero.details.slice(0, compact ? 2 : 3).map((detail) => (",
            '            <span className="bounded-chip bounded-chip--muted" key={detail}>',
            "              {detail}",
            "            </span>",
            "          ))}",
            "          {data.hero.actions.map((action) => (",
            '            <a className="bounded-cta" href={action.href ?? "#"} key={`${action.label}-${action.href ?? "inline"}`}>',
            "              {action.label}",
            "            </a>",
            "          ))}",
            "        </div>",
            "      </section>",
            "",
            "      {compact ? (",
            '        <section className="bounded-compact-shell">',
            "          {bodySections.length ? (",
            '            <section className="bounded-section-grid bounded-section-grid--compact">',
            "              {bodySections.map((section) => (",
            '                <article className="bounded-card bounded-panel" data-role={section.role} key={section.id}>',
            '                  <div className="bounded-card-head">',
            '                    <p className="bounded-kicker">{section.role}</p>',
            '                    <span className="bounded-chip bounded-chip--muted">{section.tag}</span>',
            "                  </div>",
            "                  <h2>{section.title}</h2>",
            '                  <p className="bounded-copy">{section.copy}</p>',
            '                  <div className="bounded-meta bounded-meta--inline">',
            '                    <span className="bounded-chip">{section.meta}</span>',
            "                  </div>",
            "                </article>",
            "              ))}",
            "            </section>",
            "          ) : null}",
            '          <section className="bounded-panel bounded-stack">',
            '            <p className="bounded-kicker">Interaction samples</p>',
            '            <div className="bounded-stack bounded-stack--tight">',
            "              {interactionEntries.length ? (",
            "                interactionEntries.map((entry) => (",
            '                  <article className="bounded-mini-card" key={entry.id}>',
            "                    <strong>{entry.label}</strong>",
            '                    <p>{entry.copy}</p>',
            '                    <div className="bounded-meta bounded-meta--inline">',
            "                      {entry.states.slice(0, 2).map((state) => (",
            '                        <span className="bounded-chip bounded-chip--muted" key={state}>',
            "                          {state}",
            "                        </span>",
            "                      ))}",
            "                    </div>",
            "                  </article>",
            "                ))",
            "              ) : (",
            '                <article className="bounded-mini-card"><strong>No sampled interactions</strong><p>Interaction data was not available in the capture bundle.</p></article>',
            "              )}",
            "            </div>",
            "          </section>",
            "        </section>",
            "      ) : (",
            '        <section className="bounded-layout">',
            '          <div className="bounded-main">',
            '            <section className="bounded-section-grid">',
            "              {bodySections.map((section) => (",
            '                <article className="bounded-card bounded-panel" data-role={section.role} key={section.id}>',
            '                  <div className="bounded-card-head">',
            '                    <p className="bounded-kicker">{section.role}</p>',
            '                    <span className="bounded-chip bounded-chip--muted">{section.tag}</span>',
            "                  </div>",
            "                  <h2>{section.title}</h2>",
            '                  <p className="bounded-copy">{section.copy}</p>',
            '                  <div className="bounded-meta bounded-meta--inline">',
            '                    <span className="bounded-chip">{section.meta}</span>',
            "                    {section.details.slice(0, 3).map((detail) => (",
            '                      <span className="bounded-chip bounded-chip--muted" key={detail}>',
            "                        {detail}",
            "                      </span>",
            "                    ))}",
            "                  </div>",
            "                </article>",
            "              ))}",
            "            </section>",
            "          </div>",
            "",
            '          <aside className="bounded-rail">',
            '            <section className="bounded-panel bounded-stack">',
            '              <p className="bounded-kicker">Renderer status</p>',
            '              <div className="bounded-status-row">',
            "                <strong>{data.reconstruction.strategy}</strong>",
            '                <span className="bounded-chip">{data.reconstruction.confidence}</span>',
            "              </div>",
            '              <ul className="bounded-list">',
            "                {data.reconstruction.remainingGaps.map((item) => (",
            "                  <li key={item}>{item}</li>",
            "                ))}",
            "              </ul>",
            "            </section>",
            '            <section className="bounded-panel bounded-stack">',
            '              <p className="bounded-kicker">Signals</p>',
            '              <div className="bounded-meta bounded-meta--inline">',
            "                {data.signalBits.length ? (",
            "                  data.signalBits.map((signal) => (",
            '                    <span className="bounded-chip bounded-chip--muted" key={signal}>',
            "                      {signal}",
            "                    </span>",
            "                  ))",
            "                ) : (",
            '                  <span className="bounded-chip bounded-chip--muted">No extra runtime signals were captured.</span>',
            "                )}",
            "              </div>",
            "            </section>",
            '            <section className="bounded-panel bounded-stack">',
            '              <p className="bounded-kicker">Interaction samples</p>',
            '              <div className="bounded-stack">',
            "                {interactionEntries.length ? (",
            "                  interactionEntries.map((entry) => (",
            '                    <article className="bounded-mini-card" key={entry.id}>',
            "                      <strong>{entry.label}</strong>",
            "                      <p>{entry.copy}</p>",
            '                      <div className="bounded-meta bounded-meta--inline">',
            "                        {entry.states.map((state) => (",
            '                          <span className="bounded-chip bounded-chip--muted" key={state}>',
            "                            {state}",
            "                          </span>",
            "                        ))}",
            "                      </div>",
            "                    </article>",
            "                  ))",
            "                ) : (",
            '                  <article className="bounded-mini-card"><strong>No sampled interactions</strong><p>Interaction data was not available in the capture bundle.</p></article>',
            "                )}",
            "              </div>",
            "            </section>",
            '            <section className="bounded-panel bounded-stack">',
            '              <p className="bounded-kicker">Layout rhythm</p>',
            '              <div className="bounded-stack bounded-stack--tight">',
            "                {data.reconstruction.layoutRhythm.slice(0, 6).map((item) => (",
            '                  <article className="bounded-outline-item" key={item.id}>',
            "                    <strong>{item.role}</strong>",
            "                    <p>{item.size}</p>",
            '                    <span className="bounded-outline-meta">y: {item.y ?? 0}</span>',
            "                  </article>",
            "                ))}",
            "              </div>",
            "            </section>",
            "          </aside>",
            "        </section>",
            "      )}",
            "    </main>",
            "  );",
            "}",
        ]
    )


def _interaction_cards_from_capture(capture_bundle: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    interaction_entries = captures.get("interactions", {}) if isinstance(captures.get("interactions", {}), dict) else {}
    content = interaction_entries.get("content", []) if isinstance(interaction_entries, dict) else []
    cards: list[dict[str, Any]] = []
    for index, entry in enumerate(content[:limit] if isinstance(content, list) else []):
        if not isinstance(entry, dict):
            continue
        states: list[str] = []
        hover_delta = entry.get("hoverDelta") or {}
        focus_delta = entry.get("focusDelta") or {}
        hover_state = entry.get("hoverState") or {}
        focus_state = entry.get("focusState") or {}
        type_state = entry.get("typeState") or {}
        click_state = entry.get("clickState") or {}
        click_delta = click_state.get("stateDelta") if isinstance(click_state, dict) else {}
        if isinstance(hover_delta, dict) and hover_delta:
            states.append(f"hover: {', '.join(sorted(str(key) for key in hover_delta.keys()))}")
        states.extend(_state_bits_from_snapshot(hover_state))
        if isinstance(focus_delta, dict) and focus_delta:
            states.append(f"focus: {', '.join(sorted(str(key) for key in focus_delta.keys()))}")
        states.extend(_state_bits_from_snapshot(focus_state))
        if isinstance(type_state, dict):
            before_state = type_state.get("before") or {}
            after_state = type_state.get("after") or {}
            if before_state or after_state:
                before_bits = _state_bits_from_snapshot(before_state)
                after_bits = _state_bits_from_snapshot(after_state)
                if before_bits or after_bits:
                    states.append(f"type: {' | '.join(['before ' + ', '.join(before_bits[:3]) if before_bits else 'before', 'after ' + ', '.join(after_bits[:3]) if after_bits else 'after'])}")
        if isinstance(click_delta, dict) and click_delta:
            states.append(f"click: {', '.join(sorted(str(key) for key in click_delta.keys()))}")
        if isinstance(click_state, dict):
            states.extend(_state_bits_from_snapshot(click_state.get("before")))
            states.extend(_state_bits_from_snapshot(click_state.get("after")))
            states.extend(_state_bits_from_snapshot(click_state.get("restored")))
        label = (
            _clean_text(entry.get("text"), 56)
            or _clean_text(entry.get("ariaLabel"), 56)
            or f"{str(entry.get('tag') or 'element').title()} interaction {index + 1}"
        )
        copy = _clean_text(entry.get("text"), 140) or "Interactive element sampled from runtime capture."
        cards.append(
            {
                "id": f"interaction-repair-{index + 1}",
                "label": label,
                "copy": copy,
                "tag": str(entry.get("tag") or "element"),
                "href": entry.get("href"),
                "states": states or ["interaction detected"],
                "rect": entry.get("rect"),
            }
        )
    return cards


def _trace_signal_bits(capture_bundle: dict[str, Any], limit: int = 4) -> list[str]:
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    trace_capture = captures.get("interactionTrace", {}) if isinstance(captures.get("interactionTrace", {}), dict) else {}
    trace = trace_capture.get("content", {}) if isinstance(trace_capture, dict) else {}
    steps = trace.get("steps", []) if isinstance(trace, dict) else []
    executions = trace.get("executions", []) if isinstance(trace, dict) else []
    bits: list[str] = []
    for step in steps[: limit * 2] if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        kind = _clean_text(step.get("kind"), 24)
        label = _clean_text(step.get("label"), 48)
        if kind == "scroll" and step.get("scrollY") is not None:
            _append_unique(bits, f"scroll {step.get('scrollY')}px")
        elif kind and label:
            _append_unique(bits, f"{kind} {label}")
        elif kind:
            _append_unique(bits, kind)
        if len(bits) >= limit:
            break
    if len(bits) < limit and isinstance(executions, list):
        for execution in executions:
            if not isinstance(execution, dict):
                continue
            kind = _clean_text(execution.get("kind"), 24)
            status = _clean_text(execution.get("status"), 24)
            if kind and status:
                _append_unique(bits, f"{kind} {status}")
            state_summary = execution.get("stateSummary") or {}
            if isinstance(state_summary, dict):
                before = state_summary.get("before")
                after = state_summary.get("after")
                restored = state_summary.get("restored")
                for label, snapshot in (("before", before), ("after", after), ("restored", restored)):
                    state_bits = _state_bits_from_snapshot(snapshot)
                    if state_bits:
                        _append_unique(bits, f"{kind or 'state'} {label}: {', '.join(state_bits[:3])}")
            if len(bits) >= limit:
                break
    return bits[:limit]


def _repair_css(
    base_css: str,
    app_model: dict[str, Any],
    repair_plan: dict[str, Any],
    capture_bundle: dict[str, Any],
) -> str:
    palette = app_model.get("palette", {}) if isinstance(app_model.get("palette", {}), dict) else {}
    typography = app_model.get("typography", {}) if isinstance(app_model.get("typography", {}), dict) else {}
    style_tokens = app_model.get("styleTokens", {}) if isinstance(app_model.get("styleTokens", {}), dict) else {}
    viewport = app_model.get("viewport", {}) if isinstance(app_model.get("viewport", {}), dict) else {}
    focus_checks = repair_plan.get("focus_checks", []) if isinstance(repair_plan, dict) else []
    focus_checks = [str(item) for item in focus_checks if item]
    viewport_width = int(viewport.get("width") or 1440)
    shell_width = max(360, min(viewport_width - 48, 1320))
    font_family = ((typography.get("fonts") or ["Inter, system-ui, sans-serif"])[0]) if isinstance(typography.get("fonts", []), list) else "Inter, system-ui, sans-serif"
    line_height = (typography.get("line_heights") or ["1.5"])[0] if isinstance(typography.get("line_heights", []), list) else "1.5"
    letter_spacing = (typography.get("letter_spacings") or ["-0.01em"])[0] if isinstance(typography.get("letter_spacings", []), list) else "-0.01em"
    lines = [base_css.rstrip(), "", "/* auto-repair pass */", ":root {"]
    if palette.get("text"):
        lines.append(f"  --bounded-text: {palette['text']};")
    if palette.get("surface"):
        lines.append(f"  --bounded-bg: {palette['surface']};")
    if palette.get("surfaceAlt"):
        lines.append(f"  --bounded-bg-alt: {palette['surfaceAlt']};")
    if palette.get("accent"):
        lines.append(f"  --bounded-accent: {palette['accent']};")
    lines.append(f"  --bounded-font-sans: {font_family};")
    lines.append(f"  --bounded-body-line-height: {line_height};")
    lines.append(f"  --bounded-heading-letter-spacing: {letter_spacing};")
    lines.append("}")
    lines.extend(
        [
            f".bounded-shell {{ max-width: min(100%, {shell_width}px); }}",
            ".bounded-hero { min-height: clamp(260px, 34vh, 460px); }",
            ".bounded-copy, .bounded-lede { max-width: 68ch; }",
        ]
    )
    if "screenshot" in focus_checks:
        lines.extend(
            [
                ".bounded-section-grid { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }",
                ".bounded-card[data-role=\"hero\"], .bounded-card[data-role=\"band\"] { grid-column: 1 / -1; }",
            ]
        )
    if "computed styles" in focus_checks:
        lines.extend(
            [
                ".bounded-hero h1, .bounded-card h2, .bounded-brand { font-family: var(--bounded-font-sans); letter-spacing: var(--bounded-heading-letter-spacing); }",
                ".bounded-lede, .bounded-copy, .bounded-mini-card p, .bounded-outline-item p { line-height: var(--bounded-body-line-height); }",
                ".bounded-chip { backdrop-filter: blur(12px); }",
            ]
        )
    if "interaction states" in focus_checks or "interaction trace" in focus_checks:
        lines.extend(
            [
                ".bounded-mini-card { border-color: color-mix(in srgb, var(--bounded-accent) 22%, white 8%); }",
                ".bounded-mini-card strong { letter-spacing: -0.01em; }",
            ]
        )
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    styles_capture = captures.get("styles", {}) if isinstance(captures.get("styles", {}), dict) else {}
    style_entries = styles_capture.get("content", []) if isinstance(styles_capture, dict) else []
    css_analysis = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis", {}), dict) else {}
    css_content = css_analysis.get("content", {}) if isinstance(css_analysis, dict) else {}
    body_style = css_content.get("bodyComputedStyle", {}) if isinstance(css_content.get("bodyComputedStyle", {}), dict) else {}
    presentation = app_model.get("presentation", {}) if isinstance(app_model.get("presentation", {}), dict) else {}
    compact = str(presentation.get("variant") or "") == "compact-center-stage"
    breakpoint_contexts = app_model.get("breakpoints", []) if isinstance(app_model.get("breakpoints", []), list) else []
    centered_focus_present = bool(_centered_focus_surface_summary(app_model).get("present"))
    if not style_tokens:
        style_tokens = _derive_style_tokens(style_entries if isinstance(style_entries, list) else [])
    flat_reference = str(presentation.get("styleMode") or "") == "flat-reference"
    if body_style.get("backgroundColor") or body_style.get("color"):
        lines.append("body {")
        if body_style.get("backgroundColor"):
            lines.append(f"  background-color: {body_style['backgroundColor']};")
            lines.append("  background-image: none;")
        if body_style.get("color"):
            lines.append(f"  color: {body_style['color']};")
        lines.append("}")
    section_snapshots = app_model.get("sectionStyleSnapshots", {}) if isinstance(app_model.get("sectionStyleSnapshots", {}), dict) else {}
    lines.extend(_section_style_css_lines(".bounded-hero", section_snapshots.get("hero", {})))
    lines.extend(_section_style_css_lines(".bounded-masthead, .bounded-nav", section_snapshots.get("nav", {})))
    lines.extend(_section_style_css_lines(".bounded-layout, .bounded-main, .bounded-rail, .bounded-card, .bounded-mini-card", section_snapshots.get("body", {})))
    if compact:
        lines.extend(
            [
                ".bounded-shell--compact { max-width: min(100%, 1080px); padding-top: 28px; padding-bottom: 40px; }",
                ".bounded-masthead--compact { margin-bottom: 12px; }",
                ".bounded-hero--compact { min-height: clamp(320px, 62vh, 620px); display: grid; place-items: center; text-align: center; padding: clamp(28px, 4vw, 48px); }",
                ".bounded-hero--compact .bounded-lede, .bounded-hero--compact .bounded-meta, .bounded-hero--compact .bounded-hero-actions { justify-content: center; margin-inline: auto; }",
                ".bounded-hero-orb { width: 88px; height: 88px; border-radius: 999px; margin: 0 auto 16px; background: radial-gradient(circle at 30% 30%, color-mix(in srgb, var(--bounded-accent) 74%, white 18%), color-mix(in srgb, var(--bounded-bg-alt) 72%, black 12%)); box-shadow: 0 30px 80px color-mix(in srgb, var(--bounded-accent) 28%, transparent); }",
                ".bounded-compact-shell { display: grid; gap: 16px; }",
                ".bounded-section-grid--compact { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }",
                ".bounded-hero-actions--compact .bounded-cta { min-height: 34px; }",
            ]
        )
    if flat_reference:
        lines.extend(
            [
                "body { font-size: 14px; background: linear-gradient(180deg, #1f2024 0%, #1f2024 100%); }",
                ".bounded-shell { max-width: min(100%, 1380px); padding: 8px 12px 32px; }",
                ".bounded-panel { border: 0; border-radius: 0; background: transparent; box-shadow: none; backdrop-filter: none; }",
                ".bounded-masthead { padding: 6px 8px; margin-bottom: 6px; }",
                ".bounded-hero { padding: 20px 8px 12px; margin-bottom: 8px; min-height: auto; }",
                ".bounded-hero--compact { min-height: auto; padding-top: 16px; }",
                ".bounded-hero-orb { display: none; }",
                ".bounded-hero h1 { font-size: clamp(3rem, 8vw, 5.6rem); line-height: 1; font-weight: 400; letter-spacing: 0; }",
                ".bounded-lede { max-width: 52ch; font-size: 14px; }",
                ".bounded-nav-link, .bounded-chip, .bounded-copy, .bounded-mini-card p, .bounded-outline-item p { font-size: 14px; }",
                ".bounded-chip, .bounded-chip--muted, .bounded-cta { border-radius: 0; border-color: transparent; background: transparent; color: inherit; padding-inline: 0; min-height: auto; }",
                ".bounded-cta { font-weight: 400; text-decoration: none; }",
                ".bounded-card, .bounded-stack, .bounded-mini-card, .bounded-outline-item { padding: 0; background: transparent; border: 0; }",
                ".bounded-section-grid, .bounded-compact-shell, .bounded-layout, .bounded-rail, .bounded-stack { gap: 8px; }",
            ]
        )
    if centered_focus_present:
        lines.extend(
            [
                ".bounded-shell--focus .bounded-panel { border: 0; background: transparent; box-shadow: none; backdrop-filter: none; }",
                ".bounded-shell--focus .bounded-stage, .bounded-shell--focus .bounded-layout { position: absolute !important; width: 1px !important; height: 1px !important; padding: 0 !important; margin: -1px !important; overflow: hidden !important; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; border: 0 !important; visibility: hidden !important; pointer-events: none !important; }",
            ]
        )
    for context in breakpoint_contexts:
        if not isinstance(context, dict):
            continue
        viewport_meta = context.get("viewport", {}) if isinstance(context.get("viewport", {}), dict) else {}
        max_width = int(viewport_meta.get("width") or 0)
        if max_width <= 0:
            continue
        body_style_context = context.get("bodyStyle", {}) if isinstance(context.get("bodyStyle", {}), dict) else {}
        typography_context = context.get("typography", {}) if isinstance(context.get("typography", {}), dict) else {}
        context_font_size = body_style_context.get("fontSize") or ((typography_context.get("sizes") or [None])[0] if isinstance(typography_context.get("sizes", []), list) else None)
        context_line_height = body_style_context.get("lineHeight") or ((typography_context.get("line_heights") or [None])[0] if isinstance(typography_context.get("line_heights", []), list) else None)
        context_letter_spacing = body_style_context.get("letterSpacing") or ((typography_context.get("letter_spacings") or [None])[0] if isinstance(typography_context.get("letter_spacings", []), list) else None)
        layout_sensitive = bool(context.get("layoutSensitive"))
        context_lines = [f"@media (max-width: {max_width}px) {{"]
        if max_width <= 1200:
            context_lines.extend(
                [
                    "  .bounded-shell { padding-inline: 20px; }",
                    "  .bounded-layout { grid-template-columns: minmax(0, 1fr); }",
                    "  .bounded-main, .bounded-rail { grid-column: 1 / -1; }",
                    "  .bounded-section-grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }",
                ]
            )
        if max_width <= 820 or layout_sensitive:
            context_lines.extend(
                [
                    "  .bounded-masthead { grid-template-columns: 1fr; align-items: flex-start; }",
                    "  .bounded-nav { width: 100%; overflow-x: auto; justify-content: flex-start; }",
                    "  .bounded-hero { min-height: auto; padding: 24px 18px; }",
                    "  .bounded-hero-actions, .bounded-meta--inline { gap: 8px; }",
                    "  .bounded-rail { gap: 12px; }",
                ]
            )
        if max_width <= 480:
            context_lines.extend(
                [
                    "  .bounded-shell { padding-inline: 12px; padding-top: 18px; }",
                    "  .bounded-hero h1 { font-size: clamp(2.2rem, 14vw, 4rem); }",
                    "  .bounded-card, .bounded-panel { padding: 16px; }",
                    "  .bounded-chip, .bounded-chip--muted, .bounded-cta { min-height: 32px; }",
                ]
            )
        if context_font_size:
            context_lines.append(
                "  .bounded-nav-link, .bounded-copy, .bounded-lede, .bounded-chip, .bounded-mini-card p, .bounded-outline-item p {"
                f" font-size: {context_font_size}; }}"
            )
        if context_line_height:
            context_lines.append(
                "  .bounded-lede, .bounded-copy, .bounded-mini-card p, .bounded-outline-item p {"
                f" line-height: {context_line_height}; }}"
            )
        if context_letter_spacing:
            context_lines.append(
                "  .bounded-hero h1, .bounded-card h2, .bounded-brand {"
                f" letter-spacing: {context_letter_spacing}; }}"
            )
        context_lines.append("}")
        lines.extend(context_lines)
    return "\n".join(lines).rstrip() + "\n"


def build_repair_scaffold(
    capture_bundle: dict[str, Any],
    rebuild_artifacts: dict[str, str],
    self_verify: dict[str, Any],
) -> dict[str, Any]:
    repair_plan = (self_verify.get("repair_plan") or {}) if isinstance(self_verify, dict) else {}
    if not isinstance(rebuild_artifacts, dict) or not repair_plan:
        return {
            "available": False,
            "status": "skipped",
            "reason": "Repair pass requires persisted rebuild artifacts and a repair plan.",
        }

    base_summary = _read_json(rebuild_artifacts.get("layout-summary.json"))
    base_app_model = _read_json(rebuild_artifacts.get("app-model.json"))
    if not base_summary or not base_app_model:
        return {
            "available": False,
            "status": "skipped",
            "reason": "Repair pass requires layout-summary.json and app-model.json.",
        }

    repaired_summary = json.loads(json.dumps(base_summary))
    repaired_app_model = json.loads(json.dumps(base_app_model))
    focus_checks = [str(item) for item in (repair_plan.get("focus_checks") or []) if item]
    applied_repairs: list[str] = []

    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle, dict) else {}
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    styles_capture = captures.get("styles", {}) if isinstance(captures.get("styles", {}), dict) else {}
    style_entries = styles_capture.get("content", []) if isinstance(styles_capture, dict) else []
    asset_capture = captures.get("assets", {}) if isinstance(captures.get("assets", {}), dict) else {}
    asset_content = asset_capture.get("content", {}) if isinstance(asset_capture, dict) else {}
    css_analysis_capture = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis", {}), dict) else {}
    css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
    body_style = css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis.get("bodyComputedStyle", {}), dict) else {}
    root_style = css_analysis.get("rootComputedStyle", {}) if isinstance(css_analysis.get("rootComputedStyle", {}), dict) else {}
    palette = repaired_app_model.get("palette", {}) if isinstance(repaired_app_model.get("palette", {}), dict) else {}
    typography = repaired_app_model.get("typography", {}) if isinstance(repaired_app_model.get("typography", {}), dict) else {}
    style_tokens = repaired_app_model.get("styleTokens", {}) if isinstance(repaired_app_model.get("styleTokens", {}), dict) else {}
    unique_links = _unique_link_entries(capture_bundle, limit=8)
    trace_bits = _trace_signal_bits(capture_bundle, limit=4)
    outline_sample = _outline_text_sample(base_summary, limit=3)
    breakpoint_contexts = _load_breakpoint_contexts(capture_bundle, repair_plan)
    footer_surface = _footer_surface_summary(repaired_app_model)
    centered_focus_surface = _centered_focus_surface_summary(repaired_app_model)

    if body_style.get("color"):
        palette["text"] = body_style.get("color")
        applied_repairs.append("Promoted body text color from live CSS analysis into bounded palette tokens.")
    body_background = body_style.get("backgroundColor")
    root_background = root_style.get("backgroundColor")
    body_luma = _color_luma(body_background)
    text_luma = _color_luma(body_style.get("color"))
    if body_background:
        if body_luma is not None and text_luma is not None and body_luma > 0.72 and text_luma > 0.7:
            if root_background:
                palette["surface"] = root_background
            elif palette.get("surfaceAlt"):
                palette["surface"] = palette.get("surfaceAlt")
            if not palette.get("accent"):
                palette["accent"] = body_background
            applied_repairs.append("Rejected an over-bright body background token and kept the darker root surface for screenshot fidelity.")
        else:
            palette["surface"] = body_background
            applied_repairs.append("Promoted body background color from live CSS analysis into bounded palette tokens.")
    if root_style.get("backgroundColor") and not palette.get("surfaceAlt"):
        palette["surfaceAlt"] = root_style.get("backgroundColor")
    if body_style.get("fontFamily"):
        fonts = typography.get("fonts", []) if isinstance(typography.get("fonts", []), list) else []
        fonts = [body_style.get("fontFamily"), *[item for item in fonts if item != body_style.get("fontFamily")]]
        typography["fonts"] = fonts[:3]
        applied_repairs.append("Promoted live body font family into bounded typography tokens.")
    if not style_tokens:
        style_tokens = _derive_style_tokens(style_entries if isinstance(style_entries, list) else [])
    if body_style.get("fontFamily"):
        families = style_tokens.get("font_families", []) if isinstance(style_tokens.get("font_families", []), list) else []
        families = [body_style.get("fontFamily"), *[item for item in families if item != body_style.get("fontFamily")]]
        style_tokens["font_families"] = families[:4]
    if body_style.get("lineHeight"):
        _append_unique(typography.setdefault("line_heights", []), body_style.get("lineHeight"))
        _append_unique(style_tokens.setdefault("line_heights", []), body_style.get("lineHeight"))
    if body_style.get("letterSpacing"):
        _append_unique(typography.setdefault("letter_spacings", []), body_style.get("letterSpacing"))
        _append_unique(style_tokens.setdefault("letter_spacings", []), body_style.get("letterSpacing"))
    if breakpoint_contexts:
        applied_repairs.append("Applied viewport-aware layout and typography adjustments from breakpoint capture variants.")

    section_style_snapshots = _build_section_style_snapshots(base_summary, palette, typography, style_tokens)
    if section_style_snapshots.get("sections"):
        repaired_summary["sections"] = section_style_snapshots.get("sections", [])
        repaired_app_model["sections"] = section_style_snapshots.get("sections", [])
        applied_repairs.append("Preserved and repaired per-section style snapshots for hero, nav, and body sections.")
    if unique_links:
        masthead = repaired_app_model.get("masthead", {}) if isinstance(repaired_app_model.get("masthead", {}), dict) else {}
        masthead["links"] = unique_links[:6]
        masthead["styleSnapshot"] = section_style_snapshots.get("nav", {})
        repaired_app_model["masthead"] = masthead
        applied_repairs.append("Deduplicated reusable navigation links from the full runtime interaction capture.")

    repaired_interactions = _interaction_cards_from_capture(capture_bundle, limit=10)
    if repaired_interactions:
        repaired_app_model["interactions"] = repaired_interactions
        applied_repairs.append("Expanded interaction cards from the full runtime interaction capture.")

    signal_bits = repaired_app_model.get("signalBits", []) if isinstance(repaired_app_model.get("signalBits", []), list) else []
    for bit in [*trace_bits, "auto repair pass", "css analysis"][:6]:
        _append_unique(signal_bits, bit)
    repaired_app_model["signalBits"] = signal_bits[:8]

    meta_bits = repaired_app_model.get("metaBits", []) if isinstance(repaired_app_model.get("metaBits", []), list) else []
    if isinstance(css_analysis, dict):
        _append_unique(
            meta_bits,
            f"stylesheets: {css_analysis.get('stylesheetCount', 0)} / accessible {css_analysis.get('accessibleStylesheetCount', 0)}",
        )
        _append_unique(
            meta_bits,
            f"inline styles: {css_analysis.get('inlineStyleTagCount', 0)} / style attrs {css_analysis.get('styleAttributeCount', 0)}",
        )
    repaired_app_model["metaBits"] = meta_bits[:6]

    reconstruction = repaired_app_model.get("reconstruction", {}) if isinstance(repaired_app_model.get("reconstruction", {}), dict) else {}
    reconstruction["strategy"] = "auto-repaired-next-app"
    reconstruction["appliedRepairs"] = applied_repairs[:8]
    reconstruction["repairFocus"] = focus_checks[:6]
    reconstruction["repairSource"] = repair_plan.get("target_renderer")
    reconstruction["breakpointFocus"] = [
        {
            "name": item.get("name"),
            "score": item.get("score"),
            "focus": item.get("focus"),
            "viewport": item.get("viewport"),
        }
        for item in breakpoint_contexts[:4]
    ]
    repaired_app_model["reconstruction"] = reconstruction

    hero = repaired_app_model.get("hero", {}) if isinstance(repaired_app_model.get("hero", {}), dict) else {}
    hero["eyebrow"] = "Auto-repaired reconstruction"
    hero["styleSnapshot"] = section_style_snapshots.get("hero", {})
    centered_focus_present = bool(centered_focus_surface.get("present"))
    if unique_links and not centered_focus_present:
        hero["actions"] = [{"label": item["label"], "href": item["href"], "states": []} for item in unique_links[:3]]
    elif centered_focus_present:
        hero_actions = hero.get("actions", []) if isinstance(hero.get("actions", []), list) else []
        has_focus_submit_actions = any(
            _is_google_submit_label(action.get("label"))
            for action in hero_actions
            if isinstance(action, dict)
        )
        if not has_focus_submit_actions:
            hero["actions"] = [
                {
                    "label": "Google 검색",
                    "href": None,
                    "states": [],
                    "controlTag": "input",
                    "inputType": "submit",
                },
                {
                    "label": "I’m Feeling Lucky",
                    "href": None,
                    "states": [],
                    "controlTag": "input",
                    "inputType": "submit",
                },
            ]
    if outline_sample and (
        "Bounded rebuild scaffold" in str(hero.get("copy") or "")
        or "practical app starter" in str(hero.get("copy") or "")
    ):
        hero["copy"] = outline_sample[0]
    hero_details = hero.get("details", []) if isinstance(hero.get("details", []), list) else []
    for finding in (repair_plan.get("priority_findings") or [])[:3]:
        if not isinstance(finding, dict):
            continue
        _append_unique(hero_details, finding.get("focus"))
    hero["details"] = hero_details[:6]
    repaired_app_model["hero"] = hero
    repaired_app_model["palette"] = palette
    repaired_app_model["typography"] = typography
    repaired_app_model["styleTokens"] = style_tokens
    repaired_app_model["breakpoints"] = breakpoint_contexts
    repaired_app_model["sectionStyleSnapshots"] = section_style_snapshots
    repaired_summary["sectionStyleSnapshots"] = section_style_snapshots
    section_style_by_id = section_style_snapshots.get("byId", {}) if isinstance(section_style_snapshots.get("byId", {}), dict) else {}
    body_sections = repaired_app_model.get("bodySections", []) if isinstance(repaired_app_model.get("bodySections", []), list) else []
    repaired_app_model["bodySections"] = [
        {
            **section,
            "styleSnapshot": section_style_by_id.get(str(section.get("id") or "")) or section.get("styleSnapshot", {}),
        }
        for section in body_sections
        if isinstance(section, dict)
    ]

    current_layout_mode = str(repaired_app_model.get("layoutMode") or "")
    has_focus_shell = bool(centered_focus_surface.get("present")) or bool(
        isinstance(hero.get("focusInput"), dict) and hero.get("focusInput")
    )
    has_footer_surface = bool(footer_surface.get("present"))
    should_compact = (
        ("dom snapshot" in focus_checks or "screenshot" in focus_checks)
        and current_layout_mode != "centered-focus"
        and not has_focus_shell
        and not has_footer_surface
        and len(repaired_app_model.get("sections", []) if isinstance(repaired_app_model.get("sections", []), list) else []) <= 4
        and len(body_sections) <= 2
    )
    if should_compact:
        repaired_app_model["bodySections"] = _compact_body_sections(base_summary, unique_links, trace_bits, section_style_snapshots)
        rhythm = repaired_app_model.get("reconstruction", {}).get("layoutRhythm", []) if isinstance(repaired_app_model.get("reconstruction", {}), dict) else []
        repaired_app_model.setdefault("reconstruction", {})["layoutRhythm"] = [
            {"id": "masthead", "role": "masthead", "size": "header", "y": 0},
            {"id": "hero", "role": "hero", "size": hero.get("meta"), "y": 72},
            *rhythm[:2],
        ]
        repaired_app_model["presentation"] = {
            "variant": "compact-center-stage",
            "reason": "repair-focus on screenshot or DOM structure drift",
        }
        repaired_app_model["reconstruction"]["strategy"] = "auto-repaired-compact-next-app"
        applied_repairs.append("Collapsed the renderer into a compact center-stage composition to reduce screenshot footprint drift.")
    else:
        presentation = repaired_app_model.get("presentation", {}) if isinstance(repaired_app_model.get("presentation", {}), dict) else {}
        if presentation.get("variant") == "compact-center-stage":
            presentation.pop("variant", None)
            presentation.pop("reason", None)
        if centered_focus_surface.get("present"):
            presentation["surfaceMode"] = "centered-focus-preserved"
        elif has_footer_surface:
            presentation["surfaceMode"] = "full-layout-footer-preserved"
        else:
            presentation["surfaceMode"] = "full-layout-preserved"
        repaired_app_model["presentation"] = presentation
        if "dom snapshot" in focus_checks or "screenshot" in focus_checks:
            if centered_focus_surface.get("present"):
                applied_repairs.append("Preserved the centered-focus renderer surface instead of collapsing it into a compact repair path.")
            elif has_footer_surface:
                applied_repairs.append("Preserved the full-layout renderer surface so footer links and controls remain available in repair output.")
            else:
                applied_repairs.append("Preserved the original full-layout renderer instead of collapsing it into a compact repair path.")
    if _looks_like_flat_reference(base_summary):
        presentation = repaired_app_model.get("presentation", {}) if isinstance(repaired_app_model.get("presentation", {}), dict) else {}
        presentation["styleMode"] = "flat-reference"
        repaired_app_model["presentation"] = presentation
        applied_repairs.append("Flattened panel chrome to better match a low-radius transparent reference surface.")

    repaired_summary["signals"] = json.loads(json.dumps(repaired_summary.get("signals", {})))
    repaired_summary["signals"]["auto_repair_available"] = True
    repaired_summary["repairPass"] = {
        "target_renderer": repair_plan.get("target_renderer"),
        "focus_checks": focus_checks[:6],
        "applied_repairs": applied_repairs[:8],
        "recommended_actions": (repair_plan.get("recommended_actions") or [])[:6],
        "surfaceMode": repaired_app_model.get("presentation", {}).get("surfaceMode"),
        "footerSurface": footer_surface,
        "centeredFocusSurface": centered_focus_surface,
        "compactEligible": should_compact,
    }
    repaired_summary["styleTokens"] = style_tokens
    repaired_summary["assetManifest"] = _build_asset_manifest(repaired_summary, asset_content, css_analysis, typography, style_tokens)
    repaired_summary["breakpointRepairs"] = breakpoint_contexts
    repaired_summary["layoutTokens"] = repaired_app_model.get("layoutTokens") or {}
    repaired_runtime_materialization = _build_runtime_materialization(
        repaired_summary,
        repaired_app_model,
        style_entries if isinstance(style_entries, list) else [],
    )
    repaired_summary["runtimeMaterialization"] = repaired_runtime_materialization
    repaired_app_model["runtimeMaterialization"] = repaired_runtime_materialization

    base_css = _read_text(rebuild_artifacts.get("next-app/app/globals.css"))
    repaired_css = _repair_css(base_css, repaired_app_model, repair_plan, capture_bundle)
    repaired_preview = _render_bounded_reference_page_html(repaired_app_model)
    repaired_data_ts = _render_reference_data_ts(repaired_app_model)
    repaired_fonts_css = _render_next_app_fonts_css(repaired_summary)
    repaired_layout_tsx = _render_next_app_layout_tsx(repaired_summary)
    asset_manifest = repaired_summary.get("assetManifest", {}) if isinstance(repaired_summary.get("assetManifest", {}), dict) else {}

    artifacts: dict[str, Any] = {
        "layout-summary.json": repaired_summary,
        "app-model.json": repaired_app_model,
        "app-preview.html": repaired_preview,
        "next-app/app/fonts.css": repaired_fonts_css,
        "next-app/app/globals.css": repaired_css,
        "next-app/app/layout.tsx": repaired_layout_tsx,
        "next-app/components/BoundedReferencePage.tsx": _render_bounded_reference_page_tsx(),
        "next-app/components/reference-data.ts": repaired_data_ts,
        "assets/asset-manifest.json": asset_manifest,
        "assets/font-manifest.json": asset_manifest.get("fonts", {}),
        "breakpoint-notes.json": {
            "focus_checks": focus_checks[:6],
            "variants": breakpoint_contexts,
        },
        "prompt.txt": str(repair_plan.get("prompt") or "").rstrip() + "\n",
        "repair-notes.json": {
            "target_renderer": repair_plan.get("target_renderer"),
            "focus_checks": focus_checks[:6],
            "applied_repairs": applied_repairs[:8],
            "priority_findings": (repair_plan.get("priority_findings") or [])[:6],
            "recommended_actions": (repair_plan.get("recommended_actions") or [])[:6],
        },
    }

    for key in (
        "starter.html",
        "starter.css",
        "starter.tsx",
        "next-app/app/page.tsx",
        "next-app/app/fonts.css",
    ):
        content = _read_text(rebuild_artifacts.get(key))
        if content:
            artifacts[key] = content

    artifacts["manifest.json"] = {
        "schema_version": "repair-pass.v2",
        "coverage": "bounded-auto-repair",
        "files": list(artifacts.keys()),
        "app_entrypoints": [
            "next-app/app/page.tsx",
            "next-app/components/BoundedReferencePage.tsx",
            "next-app/components/reference-data.ts",
        ],
        "target_renderer": repair_plan.get("target_renderer"),
    }

    return {
        "available": True,
        "status": "generated",
        "bounded": True,
        "reason": "A bounded auto-repair pass was derived from self-verify guidance and live capture artifacts.",
        "summary": repaired_summary,
        "artifacts": artifacts,
    }
