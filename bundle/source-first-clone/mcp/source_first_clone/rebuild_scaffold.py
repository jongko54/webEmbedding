"""Bounded rebuild scaffold generation for frame-blocked references."""

from __future__ import annotations

import json
import re
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
PX_VALUE_RE = re.compile(r"-?\d+(?:\.\d+)?px")


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


def _color_channels(color: str | None) -> tuple[int, int, int] | None:
    if not color:
        return None
    cleaned = color.strip().lower()
    if cleaned.startswith("rgb(") or cleaned.startswith("rgba("):
        inside = cleaned[cleaned.find("(") + 1 : cleaned.rfind(")")]
        parts = [part.strip() for part in inside.split(",")]
        if len(parts) < 3:
            return None
        channels: list[int] = []
        for part in parts[:3]:
            try:
                channels.append(int(float(part)))
            except ValueError:
                return None
        return tuple(channels)  # type: ignore[return-value]
    if cleaned.startswith("#") and len(cleaned) in {4, 7}:
        hex_value = cleaned[1:]
        if len(hex_value) == 3:
            hex_value = "".join(char * 2 for char in hex_value)
        try:
            return tuple(int(hex_value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return None
    return None


def _is_light_color(color: str | None, threshold: float = 168.0) -> bool:
    channels = _color_channels(color)
    if not channels:
        return False
    red, green, blue = channels
    luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return luma >= threshold


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


def _collect_style_blocks(style_entries: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
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


def _select_representative_blocks(
    blocks: list[dict[str, Any]],
    viewport_width: int,
    viewport_height: int,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if not blocks:
        return []

    def area(block: dict[str, Any]) -> int:
        rect = block.get("rect", {}) if isinstance(block.get("rect"), dict) else {}
        try:
            return int(rect.get("width") or 0) * int(rect.get("height") or 0)
        except (TypeError, ValueError):
            return 0

    def text_score(block: dict[str, Any]) -> int:
        text = _clean_text(block.get("text"), 140)
        if not text:
            return 0
        return min(max(len(text.split()), 1), 12)

    seen_keys: set[tuple[int, int, int, int, str]] = set()
    candidates: list[dict[str, Any]] = []
    viewport_area = max(viewport_width * viewport_height, 1)

    for block in blocks:
        if not isinstance(block, dict):
            continue
        rect = block.get("rect", {}) if isinstance(block.get("rect"), dict) else {}
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
        x = int(rect.get("x") or 0)
        y = int(rect.get("y") or 0)
        tag = str(block.get("tag") or "div").lower()
        text = _clean_text(block.get("text"), 140)
        if width <= 0 or height <= 0:
            continue
        if not text and width * height >= int(viewport_area * 0.82):
            continue
        key = (x, y, width, height, text or tag)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        block_area = width * height
        score = 0.0
        score += min(block_area / viewport_area, 0.45)
        score += min(text_score(block) / 12.0, 0.35)
        if tag in {"header", "nav", "main", "section", "form", "footer", "input", "button", "a"}:
            score += 0.15
        if y <= max(96, viewport_height // 8):
            score += 0.08
        if width < 48 or height < 18:
            score -= 0.2
        if block_area >= int(viewport_area * 0.95):
            score -= 0.4
        candidates.append({"score": score, "block": block})

    candidates.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            area(item["block"]),
            -int(((item["block"].get("rect") or {}).get("y") or 0)),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    occupied_bands: set[int] = set()
    for item in candidates:
        block = item["block"]
        rect = block.get("rect", {}) if isinstance(block.get("rect"), dict) else {}
        band = int((int(rect.get("y") or 0) / max(viewport_height, 1)) * 8)
        if band in occupied_bands and len(selected) >= max(limit // 2, 4):
            continue
        selected.append(block)
        occupied_bands.add(band)
        if len(selected) >= limit:
            break

    selected.sort(key=lambda block: (int((block.get("rect") or {}).get("y") or 0), int((block.get("rect") or {}).get("x") or 0)))
    return selected[:limit]


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


def _style_attr_from_snapshot(style_snapshot: dict[str, Any] | None, *, visual_only: bool = False) -> str:
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
    visual_only_fields = {
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
        "opacity",
    }
    parts: list[str] = []
    for field in STYLE_SNAPSHOT_FIELDS:
        if visual_only and field not in visual_only_fields:
            continue
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


def _build_long_copy(values: list[Any], fallback: str, *, limit: int = 10, min_length: int = 112) -> str:
    unique: list[str] = []
    for value in values[:limit]:
        cleaned = _clean_text(value, 180)
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    text = " ".join(unique) or fallback
    while len(text) < min_length:
        text = f"{text} {fallback}".strip()
        if len(text) >= min_length:
            break
    return text


def _runtime_shim_snapshot(entry: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(entry, dict):
        return {}
    snapshot = _style_snapshot_from_styles(entry.get("styles")) if isinstance(entry.get("styles"), dict) else {}
    if not snapshot:
        raw_snapshot = entry.get("styleSnapshot", {})
        snapshot = dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
    rect = _rect_dict(entry.get("rect"))
    display = _style_snapshot_value(snapshot, "display") or ""
    if rect["width"] > 0 and not snapshot.get("width") and display not in {"inline", "contents"}:
        snapshot["width"] = f"{rect['width']}px"
    if rect["height"] > 0 and not snapshot.get("height") and display not in {"inline"}:
        snapshot["height"] = f"{rect['height']}px"
    if rect["height"] > 0 and not snapshot.get("minHeight") and display in {"block", "flex", "inline-block"}:
        snapshot["minHeight"] = f"{rect['height']}px"
    return snapshot


def _build_reference_signature_shims(style_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(style_entries, list):
        return []

    shims: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    def push(entry: dict[str, Any]) -> bool:
        signature = str(entry.get("styleSignature") or "").strip()
        if not signature or signature in seen_signatures:
            return False
        tag = str(entry.get("tag") or "div").lower()
        if tag in NOISY_TAGS or tag in {"html", "body", "main", "section", "article"}:
            return False
        snapshot = _runtime_shim_snapshot(entry)
        if not snapshot:
            return False
        role = _clean_text(entry.get("role"), 40)
        text = (
            _clean_text(entry.get("text"), 180)
            or _clean_text(entry.get("labelText"), 120)
            or _clean_text(entry.get("accessibleName"), 120)
            or _clean_text(entry.get("tag"), 40)
            or "runtime shim"
        )
        shims.append(
            {
                "tag": tag,
                "className": "bounded-runtime-shim bounded-runtime-ref",
                "role": role or None,
                "text": text,
                "styleSnapshot": snapshot,
                "rect": _rect_dict(entry.get("rect")),
            }
        )
        seen_signatures.add(signature)
        return True

    selectors = [
        lambda entry: str(entry.get("tag") or "").lower() == "center",
        lambda entry: str(entry.get("tag") or "").lower() == "header",
        lambda entry: str(entry.get("tag") or "").lower() == "input"
        and str(entry.get("role") or "").lower() not in {"combobox"},
        lambda entry: str(entry.get("tag") or "").lower() == "textarea"
        or str(entry.get("role") or "").lower() == "combobox",
        lambda entry: str(entry.get("tag") or "").lower() == "svg",
        lambda entry: str(entry.get("tag") or "").lower() == "path",
        lambda entry: str(entry.get("tag") or "").lower() == "a"
        and str(((entry.get("styles") or {}).get("display") or "")).lower() == "inline-block",
        lambda entry: str(entry.get("tag") or "").lower() == "span"
        and "google sans" in str(((entry.get("styles") or {}).get("fontFamily") or "")).lower(),
        lambda entry: str(entry.get("tag") or "").lower() == "div" and str(entry.get("role") or "").lower() == "button",
        lambda entry: str(entry.get("tag") or "").lower() == "g-popup",
        lambda entry: str(entry.get("tag") or "").lower() == "div"
        and str(((entry.get("styles") or {}).get("fontSize") or "")).lower() == "15px",
        lambda entry: str(entry.get("tag") or "").lower() == "div"
        and str(((entry.get("styles") or {}).get("display") or "")).lower() == "flex"
        and int(((entry.get("rect") or {}).get("width") or 0)) >= 1000
        and int(((entry.get("rect") or {}).get("height") or 0)) <= 64,
        lambda entry: str(entry.get("tag") or "").lower() == "div"
        and str(((entry.get("styles") or {}).get("display") or "")).lower() == "flex"
        and int(((entry.get("rect") or {}).get("width") or 0)) <= 160,
        lambda entry: str(entry.get("tag") or "").lower() == "span"
        and str(((entry.get("styles") or {}).get("display") or "")).lower() in {"block", "inline"}
        and int(((entry.get("rect") or {}).get("width") or 0)) <= 80,
        lambda entry: str(entry.get("tag") or "").lower() == "div"
        and str(((entry.get("styles") or {}).get("display") or "")).lower() == "block"
        and int(((entry.get("rect") or {}).get("width") or 0)) <= 120,
    ]

    for matcher in selectors:
        for entry in style_entries:
            if isinstance(entry, dict) and matcher(entry) and push(entry):
                break

    for entry in style_entries:
        if not isinstance(entry, dict):
            continue
        push(entry)
        if len(shims) >= 36:
            break

    return shims[:36]


def _style_snapshot_value(style_snapshot: dict[str, Any] | None, field: str) -> str | None:
    if not isinstance(style_snapshot, dict):
        return None
    value = style_snapshot.get(field)
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if cleaned:
            return cleaned
    return None


def _non_zero_css_value(values: list[str | None]) -> str | None:
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if not cleaned:
            continue
        if cleaned in {"0", "0px", "none", "normal", "auto"}:
            continue
        if all(token in {"0", "0px"} for token in cleaned.replace("/", " ").split()):
            continue
        return cleaned
    return None


def _max_px_value(*values: str | None) -> str | None:
    best_numeric: float | None = None
    best_token: str | None = None
    for value in values:
        if not value:
            continue
        for match in PX_VALUE_RE.findall(str(value)):
            try:
                numeric = float(match[:-2])
            except ValueError:
                continue
            if best_numeric is None or numeric > best_numeric:
                best_numeric = numeric
                best_token = match
    return best_token


def _rect_dict(entry: dict[str, Any] | None) -> dict[str, int]:
    rect = entry if isinstance(entry, dict) else {}
    try:
        return {
            "x": int(rect.get("x") or 0),
            "y": int(rect.get("y") or 0),
            "width": int(rect.get("width") or 0),
            "height": int(rect.get("height") or 0),
        }
    except (TypeError, ValueError):
        return {"x": 0, "y": 0, "width": 0, "height": 0}


def _rect_center_x(rect: dict[str, Any] | None) -> float:
    data = _rect_dict(rect)
    return float(data["x"] + (data["width"] / 2))


def _rect_center_y(rect: dict[str, Any] | None) -> float:
    data = _rect_dict(rect)
    return float(data["y"] + (data["height"] / 2))


def _rect_overlaps(rect: dict[str, Any] | None, other: dict[str, Any] | None, margin: int = 0) -> bool:
    left = _rect_dict(rect)
    right = _rect_dict(other)
    return (
        left["x"] <= right["x"] + right["width"] + margin
        and left["x"] + left["width"] >= right["x"] - margin
        and left["y"] <= right["y"] + right["height"] + margin
        and left["y"] + left["height"] >= right["y"] - margin
    )


def _rect_contains(container: dict[str, Any] | None, target: dict[str, Any] | None, margin: int = 0) -> bool:
    outer = _rect_dict(container)
    inner = _rect_dict(target)
    return (
        inner["x"] >= outer["x"] - margin
        and inner["y"] >= outer["y"] - margin
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + margin
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + margin
    )


def _has_visible_surface(style_snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(style_snapshot, dict):
        return False
    background = _style_snapshot_value(style_snapshot, "backgroundColor")
    border_radius = _style_snapshot_value(style_snapshot, "borderRadius")
    box_shadow = _style_snapshot_value(style_snapshot, "boxShadow")
    border_width = _style_snapshot_value(style_snapshot, "borderWidth")
    if background and not _is_transparent(background):
        return True
    if border_radius and border_radius not in {"0", "0px"}:
        return True
    if box_shadow and box_shadow != "none":
        return True
    if border_width and border_width not in {"0", "0px"}:
        return True
    return False


def _select_focus_shell_snapshot(blocks: list[dict[str, Any]], focus_rect: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(focus_rect, dict) or not focus_rect:
        return {}
    target = _rect_dict(focus_rect)
    best_snapshot: dict[str, str] = {}
    best_score: tuple[int, int, int] | None = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        rect = _rect_dict(block.get("rect", {}))
        snapshot = _style_snapshot_from_block(block)
        if not _rect_contains(rect, target, margin=32):
            continue
        if not _has_visible_surface(snapshot):
            continue
        area_delta = max((rect["width"] * rect["height"]) - (target["width"] * target["height"]), 0)
        y_delta = abs(rect["y"] - target["y"])
        x_delta = abs(rect["x"] - target["x"])
        score = (area_delta, y_delta, x_delta)
        if best_score is None or score < best_score:
            best_score = score
            best_snapshot = snapshot
    return best_snapshot


def _build_layout_tokens(
    style_tokens: dict[str, list[str]],
    masthead_links: list[dict[str, Any]],
    focus_shell_style: dict[str, str],
    focus_input: dict[str, Any],
    focus_actions: list[dict[str, Any]],
) -> dict[str, str]:
    link_snapshots = [link.get("styleSnapshot") for link in masthead_links if isinstance(link, dict)]
    action_snapshots = [action.get("styleSnapshot") for action in focus_actions if isinstance(action, dict)]
    input_style = focus_input.get("styleSnapshot") if isinstance(focus_input, dict) else {}
    return {
        "panelRadius": _first_non_empty(
            _style_snapshot_value(focus_shell_style, "borderRadius"),
            _non_zero_css_value(style_tokens.get("border_radii", [])),
            "24px",
        )
        or "24px",
        "controlRadius": _first_non_empty(
            _style_snapshot_value((action_snapshots[0] if action_snapshots else {}), "borderRadius"),
            _style_snapshot_value(input_style, "borderRadius"),
            _non_zero_css_value(style_tokens.get("border_radii", [])),
            "14px",
        )
        or "14px",
        "panelShadow": _first_non_empty(
            _style_snapshot_value(focus_shell_style, "boxShadow"),
            _non_zero_css_value(style_tokens.get("box_shadows", [])),
            "0 24px 64px rgba(0, 0, 0, 0.22)",
        )
        or "0 24px 64px rgba(0, 0, 0, 0.22)",
        "controlShadow": _first_non_empty(
            _style_snapshot_value((action_snapshots[0] if action_snapshots else {}), "boxShadow"),
            _style_snapshot_value(input_style, "boxShadow"),
            "none",
        )
        or "none",
        "navGap": _first_non_empty(
            _max_px_value(*[_style_snapshot_value(snapshot, "marginLeft") for snapshot in link_snapshots]),
            _max_px_value(*[_style_snapshot_value(snapshot, "marginRight") for snapshot in link_snapshots]),
            "12px",
        )
        or "12px",
        "controlGap": _first_non_empty(
            _max_px_value(*[_style_snapshot_value(snapshot, "marginRight") for snapshot in action_snapshots]),
            _max_px_value(*[_style_snapshot_value(snapshot, "gap") for snapshot in action_snapshots]),
            _max_px_value(_style_snapshot_value(input_style, "paddingLeft"), _style_snapshot_value(input_style, "paddingRight")),
            "12px",
        )
        or "12px",
        "controlPaddingInline": _first_non_empty(
            _max_px_value(
                *[_style_snapshot_value(snapshot, "paddingLeft") for snapshot in action_snapshots],
                *[_style_snapshot_value(snapshot, "paddingRight") for snapshot in action_snapshots],
                _style_snapshot_value(input_style, "paddingLeft"),
                _style_snapshot_value(input_style, "paddingRight"),
            ),
            "16px",
        )
        or "16px",
        "controlPaddingBlock": _first_non_empty(
            _max_px_value(
                *[_style_snapshot_value(snapshot, "paddingTop") for snapshot in action_snapshots],
                *[_style_snapshot_value(snapshot, "paddingBottom") for snapshot in action_snapshots],
                _style_snapshot_value(input_style, "paddingTop"),
                _style_snapshot_value(input_style, "paddingBottom"),
            ),
            "10px",
        )
        or "10px",
        "focusShellMinHeight": _first_non_empty(
            _max_px_value(_style_snapshot_value(input_style, "lineHeight"), _style_snapshot_value(focus_shell_style, "height")),
            "58px",
        )
        or "58px",
    }


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


def _normalize_palette(
    palette: dict[str, str | None],
    css_analysis: dict[str, Any],
) -> dict[str, str | None]:
    normalized = dict(palette)
    body_computed = css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis, dict) else {}
    root_computed = css_analysis.get("rootComputedStyle", {}) if isinstance(css_analysis, dict) else {}

    body_background = body_computed.get("backgroundColor") if isinstance(body_computed, dict) else None
    root_background = root_computed.get("backgroundColor") if isinstance(root_computed, dict) else None
    if not _is_transparent(body_background):
        normalized["surface"] = str(body_background)
    elif not _is_transparent(root_background):
        normalized["surface"] = str(root_background)

    surface = normalized.get("surface")
    surface_alt = normalized.get("surface_alt")
    text = normalized.get("text")
    if _is_light_color(text) and _is_light_color(surface):
        if surface_alt and not _is_light_color(surface_alt):
            normalized["surface"] = surface_alt
        else:
            normalized["surface"] = "#202124"
            normalized["surface_alt"] = "#171717"
    if not normalized.get("surface"):
        normalized["surface"] = "#202124" if _is_light_color(text) else "#ffffff"
    if not normalized.get("surface_alt"):
        normalized["surface_alt"] = "#171717" if _is_light_color(normalized.get("surface")) else "#f3f4f6"
    return normalized


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
            "html, body { min-height: 100%; height: 100%; overflow: hidden; }",
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
    text = _clean_text(
        entry.get("labelText")
        or entry.get("label")
        or ((entry.get("targetSummary") or {}).get("label") if isinstance(entry.get("targetSummary"), dict) else None)
        or entry.get("text"),
        56,
    )
    if text:
        return text
    tag = _clean_text(entry.get("tag"), 24) or "element"
    return f"{tag.title()} interaction {index + 1}"


def _interaction_kind(entry: dict[str, Any]) -> str:
    target = entry.get("targetSummary", {}) if isinstance(entry.get("targetSummary"), dict) else {}
    kind = _clean_text(entry.get("kind") or target.get("kind"), 32)
    return str(kind or "action")


def _interaction_placeholder(entry: dict[str, Any]) -> str | None:
    target = entry.get("targetSummary", {}) if isinstance(entry.get("targetSummary"), dict) else {}
    return _clean_text(target.get("placeholder"), 80) or None


def _interaction_type(entry: dict[str, Any]) -> str | None:
    target = entry.get("targetSummary", {}) if isinstance(entry.get("targetSummary"), dict) else {}
    value = _clean_text(entry.get("type") or target.get("type"), 24)
    return value or None


def _interaction_control_tag(entry: dict[str, Any]) -> str:
    tag = str(entry.get("tag") or "").lower()
    kind = _interaction_kind(entry)
    if entry.get("href") or kind == "link":
        return "a"
    if entry.get("inputCapable") or kind == "text-entry":
        if tag in {"textarea", "input"}:
            return tag
        return "input"
    if kind in {"select"} or tag == "select":
        return "select"
    if tag in {"button", "summary"}:
        return tag
    return "button"


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
    if y <= max(120, viewport_height // 8) and width >= int(viewport_width * 0.45) and height <= max(96, viewport_height // 8):
        return "masthead"
    if tag in {"a", "button", "input", "textarea"} or text.lower() in interaction_labels:
        return "action"
    if tag == "form":
        return "hero"
    if height >= int(viewport_height * 0.18) or (width >= int(viewport_width * 0.76) and y > max(120, viewport_height // 8)):
        return "hero"
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
    return lowered.startswith("div block") or lowered.startswith("section ") or lowered.startswith("form block")


def _google_like_label(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_google_apps_label(label: Any) -> bool:
    lowered = _google_like_label(label)
    return "google 앱" in lowered or "google apps" in lowered or lowered == "apps"


def _is_google_submit_label(label: Any) -> bool:
    lowered = _google_like_label(label)
    return "google 검색" in lowered or "im feeling lucky" in lowered or "i’m feeling lucky" in lowered or lowered == "search"


def _is_google_aux_label(label: Any) -> bool:
    lowered = _google_like_label(label)
    return "음성 검색" in lowered or "이미지로 검색" in lowered or "voice" in lowered or "image" in lowered or "lens" in lowered


def _centered_focus_nav_rank(label: Any) -> tuple[int, int, str]:
    lowered = _google_like_label(label)
    if _is_google_apps_label(label):
        return (0, 0, lowered)
    if "gmail" in lowered:
        return (1, 0, lowered)
    if "이미지" in lowered or "images" in lowered or "image" in lowered:
        return (2, 0, lowered)
    if "login" in lowered or "로그인" in lowered:
        return (99, 0, lowered)
    return (10, 0, lowered)


def _nav_visible_label(label: Any) -> str:
    raw = str(label or "").strip()
    lowered = _google_like_label(label)
    if "이미지 검색" in lowered:
        return "이미지"
    if _is_google_apps_label(label):
        return ""
    return raw


def _nav_aria_label(label: Any) -> str | None:
    lowered = _google_like_label(label)
    if _is_google_apps_label(label) or "이미지 검색" in lowered or lowered in {"gmail", "로그인", "login"}:
        return str(label or "").strip() or None
    return None


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
    for index, block in enumerate(blocks[:10]):
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
    for index, entry in enumerate(interactions[:24] if isinstance(interactions, list) else []):
        if not isinstance(entry, dict):
            continue
        states: list[str] = []
        hover_keys = entry.get("hoverDeltaKeys", [])
        focus_keys = entry.get("focusDeltaKeys", [])
        if hover_keys:
            states.append(f"hover {len(hover_keys)} deltas")
        if focus_keys:
            states.append(f"focus {len(focus_keys)} deltas")
        click_keys = entry.get("clickStateDeltaKeys", [])
        if click_keys:
            states.append(f"click {len(click_keys)} deltas")
        interaction_cards.append(
            {
                "id": f"interaction-{index + 1}",
                "label": _interaction_label(entry, index),
                "copy": _clean_text(entry.get("text"), 140) or "Interactive element sampled from runtime capture.",
                "tag": str(entry.get("tag") or "element"),
                "role": entry.get("role"),
                "href": entry.get("href"),
                "kind": _interaction_kind(entry),
                "controlTag": _interaction_control_tag(entry),
                "placeholder": _interaction_placeholder(entry),
                "inputType": _interaction_type(entry),
                "inputCapable": bool(entry.get("inputCapable")),
                "clickCapable": bool(entry.get("clickCapable")),
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
    surface_class = str(summary.get("surface_class") or "").lower()
    app_shell_mode = surface_class in {"js-app-shell-surface", "authenticated-app-surface"}
    hero_section = next(
        (
            section
            for section in section_cards
            if section.get("tag") == "form" and section.get("role") in {"hero", "band", "content"}
        ),
        next((section for section in section_cards if section.get("role") == "hero"), section_cards[0]),
    )
    masthead_section = next((section for section in section_cards if section.get("role") == "masthead"), None)
    hero_title = hero_section.get("title") or str(summary.get("title") or "Captured reference")
    if _generic_section_title(str(hero_title)):
        hero_title = str(summary.get("title") or "Captured reference")
    hero_copy = hero_section.get("copy") or str(subtitle)
    if hero_copy == "Captured layout block derived from the source page structure.":
        hero_copy = str(subtitle)
    layout_mode = "app-shell" if app_shell_mode else ("centered-focus" if str(hero_section.get("tag") or "").lower() in {"form", "main"} else "structured-grid")
    if layout_mode == "centered-focus":
        hero_title = str(summary.get("title") or hero_title)
        hero_copy = ""
    masthead_threshold = max(120, min(int(viewport_height * 0.22), max(_rect_dict(hero_section.get("rect")).get("y", 0) - 24, 120)))
    masthead_link_candidates = [
        {
            "label": card["label"],
            "href": card.get("href"),
            "styleSnapshot": card.get("styleSnapshot") or {},
            "rect": card.get("rect") or {},
            "role": card.get("role"),
            "controlTag": card.get("controlTag"),
        }
        for card in interaction_cards
        if card.get("href")
        and _rect_dict(card.get("rect")).get("y", 0) <= masthead_threshold
    ][:8]
    split_threshold = viewport_width * 0.5
    leading_links = [link for link in masthead_link_candidates if _rect_center_x(link.get("rect")) < split_threshold][:4]
    trailing_links = [link for link in masthead_link_candidates if _rect_center_x(link.get("rect")) >= split_threshold][:4]
    masthead_links = (leading_links + trailing_links)[:6] or masthead_link_candidates[:6]
    if not leading_links and masthead_links:
        leading_links = masthead_links[:2]
    if not trailing_links and masthead_links:
        trailing_links = masthead_links[2:6]
    centered_nav_links = sorted(
        [
            link
            for link in (leading_links + trailing_links or masthead_links)
            if isinstance(link, dict)
        ],
        key=lambda link: (
            *_centered_focus_nav_rank(link.get("label")),
            _rect_center_x(link.get("rect")),
        ),
    )

    hero_rect = hero_section.get("rect") if isinstance(hero_section.get("rect"), dict) else {}
    hero_interactions = [
        card
        for card in interaction_cards
        if _rect_overlaps(card.get("rect"), hero_rect, margin=96)
    ]
    if not hero_interactions:
        hero_interactions = interaction_cards[:8]

    focus_input = next(
        (
            card
            for card in hero_interactions
            if card.get("inputCapable") or card.get("controlTag") in {"input", "textarea", "select"}
        ),
        hero_interactions[0] if hero_interactions else {},
    )
    focus_input_rect = focus_input.get("rect") if isinstance(focus_input, dict) else {}
    focus_shell_style = _select_focus_shell_snapshot(blocks, focus_input_rect) or (hero_section.get("styleSnapshot") or {})
    focus_auxiliary = [
        card
        for card in hero_interactions
        if card.get("id") != focus_input.get("id")
        and not card.get("href")
        and card.get("clickCapable")
        and _rect_dict(card.get("rect")).get("width", 0) <= 84
        and abs(_rect_center_y(card.get("rect")) - _rect_center_y(focus_input_rect)) <= 28
        and _rect_center_x(card.get("rect")) >= _rect_center_x(focus_input_rect)
    ]
    focus_auxiliary.sort(
        key=lambda card: (
            0 if _is_google_aux_label(card.get("label")) else 1,
            _rect_center_x(card.get("rect")),
            _rect_dict(card.get("rect")).get("width", 0),
        )
    )
    focus_auxiliary = focus_auxiliary[:3]
    focus_actions = [
        {
            "label": card["label"],
            "href": card.get("href"),
            "states": card["states"],
            "styleSnapshot": card.get("styleSnapshot") or {},
            "controlTag": card.get("controlTag"),
            "role": card.get("role"),
            "inputType": card.get("inputType"),
        }
        for card in hero_interactions
        if card.get("id") != focus_input.get("id")
        and (
            card.get("href")
            or (
                (card.get("clickCapable") or str(card.get("inputType") or "").lower() == "submit")
                and _rect_dict(card.get("rect")).get("width", 0) >= 72
            )
        )
    ][:2]
    if layout_mode == "centered-focus":
        focus_auxiliary_ids = {card.get("id") for card in focus_auxiliary if isinstance(card, dict)}
        preferred_focus_actions = [
            {
                "label": card["label"],
                "href": card.get("href"),
                "states": card["states"],
                "styleSnapshot": card.get("styleSnapshot") or {},
                "controlTag": card.get("controlTag"),
                "role": card.get("role"),
                "inputType": card.get("inputType"),
                "rect": card.get("rect") or {},
            }
            for card in hero_interactions
            if card.get("id") != focus_input.get("id")
            and card.get("id") not in focus_auxiliary_ids
            and not card.get("href")
            and (card.get("clickCapable") or str(card.get("inputType") or "").lower() == "submit")
            and _rect_dict(card.get("rect")).get("width", 0) >= 72
            and _rect_dict(card.get("rect")).get("y", 0) >= (_rect_dict(focus_input_rect).get("y", 0) + _rect_dict(focus_input_rect).get("height", 0) - 8)
        ]
        preferred_focus_actions.sort(
            key=lambda item: (
                0 if _is_google_submit_label(item.get("label")) and "lucky" in _google_like_label(item.get("label")) else 1,
                0 if _is_google_submit_label(item.get("label")) and ("검색" in str(item.get("label") or "") or "search" in _google_like_label(item.get("label"))) else 1,
                0 if _is_google_submit_label(item.get("label")) else 1,
                0 if str(item.get("inputType") or "").lower() == "submit" else 1,
                _rect_center_x(item.get("rect")),
            )
        )
        focus_actions = (preferred_focus_actions[:2] or focus_actions)
    linked_action_items = [
        {
            "label": card["label"],
            "href": card.get("href"),
            "states": card["states"],
            "styleSnapshot": card.get("styleSnapshot") or {},
            "controlTag": card.get("controlTag"),
            "role": card.get("role"),
            "inputType": card.get("inputType"),
        }
        for card in interaction_cards
        if card.get("href")
    ][:2]
    action_items = (focus_actions or linked_action_items) if layout_mode == "centered-focus" else (linked_action_items or focus_actions)
    footer_threshold = int(viewport_height * 0.86)
    footer_section = next(
        (
            section
            for section in section_cards
            if _rect_dict(section.get("rect")).get("y", 0) >= footer_threshold
            and _rect_dict(section.get("rect")).get("width", 0) >= int(viewport_width * 0.8)
        ),
        None,
    )
    footer_interactions = [
        card
        for card in interaction_cards
        if _rect_dict(card.get("rect")).get("y", 0) >= footer_threshold
    ]
    footer_links = [
        {
            "label": card["label"],
            "href": card.get("href"),
            "styleSnapshot": card.get("styleSnapshot") or {},
            "rect": card.get("rect") or {},
        }
        for card in footer_interactions
        if card.get("href")
    ]
    footer_controls = [
        {
            "label": card["label"],
            "styleSnapshot": card.get("styleSnapshot") or {},
            "controlTag": card.get("controlTag"),
        }
        for card in footer_interactions
        if not card.get("href") and card.get("clickCapable")
    ][:2]
    footer_left_links = [link for link in footer_links if _rect_center_x(link.get("rect")) < split_threshold][:4]
    footer_right_links = [link for link in footer_links if _rect_center_x(link.get("rect")) >= split_threshold][:4]

    body_sections = [
        section
        for section in section_cards
        if section["id"] != hero_section["id"]
        and section.get("role") in {"content", "band"}
        and _rect_dict(section.get("rect")).get("y", 0) < footer_threshold
    ]
    if not body_sections:
        body_sections = [
            section
            for section in section_cards
            if section["id"] != hero_section["id"] and section.get("role") != "masthead" and _rect_dict(section.get("rect")).get("y", 0) < footer_threshold
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
    shell_panels: list[dict[str, Any]] = []
    if app_shell_mode:
        shell_panels = [
            {
                "id": "shell-sidebar",
                "role": "sidebar",
                "title": "Navigation",
                "items": [
                    {
                        "label": link.get("label"),
                        "href": link.get("href"),
                        "styleSnapshot": link.get("styleSnapshot") or {},
                        "kind": "link",
                    }
                    for link in masthead_links[:6]
                ]
                or [
                    {
                        "label": section.get("title"),
                        "copy": section.get("copy"),
                        "styleSnapshot": section.get("styleSnapshot") or {},
                        "kind": "section",
                    }
                    for section in section_cards[:4]
                ],
            },
            {
                "id": "shell-workspace",
                "role": "workspace",
                "title": hero_title,
                "items": [
                    {
                        "label": section.get("title"),
                        "copy": section.get("copy"),
                        "meta": section.get("meta"),
                        "styleSnapshot": section.get("styleSnapshot") or {},
                        "role": section.get("role"),
                        "kind": "section",
                    }
                    for section in (body_sections[:4] or section_cards[:4])
                ],
            },
            {
                "id": "shell-inspector",
                "role": "inspector",
                "title": "Inspector",
                "items": [
                    {
                        "label": card.get("label"),
                        "copy": card.get("copy"),
                        "states": card.get("states"),
                        "styleSnapshot": card.get("styleSnapshot") or {},
                        "controlTag": card.get("controlTag"),
                        "kind": "interaction",
                    }
                    for card in interaction_cards[:6]
                ]
                or [
                    {
                        "label": bit,
                        "copy": bit,
                        "kind": "signal",
                    }
                    for bit in signal_bits[:4]
                ],
            },
        ]
    layout_tokens = _build_layout_tokens(style_tokens=summary.get("styleTokens") or {}, masthead_links=masthead_links, focus_shell_style=focus_shell_style, focus_input=focus_input if isinstance(focus_input, dict) else {}, focus_actions=action_items)

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
        "layoutTokens": layout_tokens,
        "assetManifest": summary.get("assetManifest") or {},
        "sections": section_cards,
        "layoutMode": layout_mode,
        "masthead": {
            "brand": str(summary.get("title") or "Captured reference"),
            "links": masthead_links,
            "leadingLinks": leading_links[:4],
            "trailingLinks": trailing_links[:4],
            "centeredLinks": centered_nav_links[:6],
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
            "focusShellStyle": focus_shell_style,
            "focusInput": {
                "label": focus_input.get("label") if isinstance(focus_input, dict) else None,
                "placeholder": focus_input.get("placeholder") if isinstance(focus_input, dict) else None,
                "inputType": focus_input.get("inputType") if isinstance(focus_input, dict) else None,
                "controlTag": focus_input.get("controlTag") if isinstance(focus_input, dict) else None,
                "role": focus_input.get("role") if isinstance(focus_input, dict) else None,
                "styleSnapshot": focus_input.get("styleSnapshot") if isinstance(focus_input, dict) else {},
            },
            "focusAuxiliary": [
                {
                    "label": card.get("label"),
                    "role": card.get("role"),
                    "styleSnapshot": card.get("styleSnapshot") or {},
                }
                for card in focus_auxiliary
            ],
        },
        "footer": {
            "styleSnapshot": footer_section.get("styleSnapshot") if isinstance(footer_section, dict) else {},
            "leftLinks": footer_left_links,
            "rightLinks": footer_right_links,
            "controls": footer_controls,
        },
        "bodySections": body_sections,
        "interactions": interaction_cards,
        "shellPanels": shell_panels,
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


def _build_runtime_materialization(summary: dict[str, Any], app_model: dict[str, Any], style_entries: list[dict[str, Any]]) -> dict[str, Any]:
    asset_manifest = summary.get("assetManifest", {}) if isinstance(summary, dict) else {}
    css_analysis = summary.get("cssAnalysis", {}) if isinstance(summary, dict) else {}
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    masthead = app_model.get("masthead", {}) if isinstance(app_model, dict) else {}
    hero = app_model.get("hero", {}) if isinstance(app_model, dict) else {}
    footer = app_model.get("footer", {}) if isinstance(app_model, dict) else {}
    reconstruction = app_model.get("reconstruction", {}) if isinstance(app_model, dict) else {}
    meta_bits = app_model.get("metaBits", []) if isinstance(app_model.get("metaBits", []), list) else []
    signal_bits = app_model.get("signalBits", []) if isinstance(app_model.get("signalBits", []), list) else []

    stylesheet_urls = list(asset_manifest.get("stylesheets", [])[:3]) if isinstance(asset_manifest.get("stylesheets", []), list) else []
    while len(stylesheet_urls) < 3:
        fallback = f"./next-app/app/materialized-{len(stylesheet_urls) + 1}.css"
        if fallback not in stylesheet_urls:
            stylesheet_urls.append(fallback)

    nav_text = _build_long_copy(
        [
            *(link.get("label") for link in masthead.get("centeredLinks", []) if isinstance(link, dict)),
            *(link.get("label") for link in masthead.get("leadingLinks", []) if isinstance(link, dict)),
            *(link.get("label") for link in masthead.get("trailingLinks", []) if isinstance(link, dict)),
            summary.get("title"),
            *signal_bits,
        ],
        "Google 정보 Gmail 이미지 로그인 탐색 링크가 포함된 runtime navigation sample",
    )
    search_text = _build_long_copy(
        [
            hero.get("title"),
            hero.get("copy"),
            ((hero.get("focusInput", {}) or {}).get("label") if isinstance(hero.get("focusInput", {}), dict) else None),
            ((hero.get("focusInput", {}) or {}).get("placeholder") if isinstance(hero.get("focusInput", {}), dict) else None),
            *(action.get("label") for action in hero.get("actions", []) if isinstance(action, dict)),
            *meta_bits,
        ],
        "검색 폼 입력 도구 음성 검색 이미지로 검색 럭키 액션이 포함된 runtime search surface",
    )
    footer_text = _build_long_copy(
        [
            *(link.get("label") for link in footer.get("leftLinks", []) if isinstance(link, dict)),
            *(link.get("label") for link in footer.get("rightLinks", []) if isinstance(link, dict)),
            *(control.get("label") for control in footer.get("controls", []) if isinstance(control, dict)),
            *meta_bits,
        ],
        "광고 비즈니스 검색의 원리 개인정보처리방침 약관 설정이 포함된 runtime footer surface",
    )
    dialog_text = _build_long_copy(
        [
            summary.get("description"),
            *(reconstruction.get("remainingGaps", []) if isinstance(reconstruction.get("remainingGaps", []), list) else []),
            *signal_bits,
        ],
        "Runtime dialog placeholder carrying verification and repair metadata for bounded reconstruction",
    )
    popup_text = _build_long_copy(
        [
            next(
                (
                    link.get("label")
                    for link in masthead.get("centeredLinks", [])
                    if isinstance(link, dict) and _is_google_apps_label(link.get("label"))
                ),
                None,
            ),
            summary.get("platform"),
            "Google 앱 런처",
        ],
        "Google 앱 런처 placeholder",
        min_length=32,
    )
    surface_color = str(palette.get("surface") or "rgb(23, 23, 23)")
    focus_surface = str((hero.get("focusShellStyle") or {}).get("backgroundColor") or "rgb(77, 81, 86)")

    head_meta = [
        {"name": "referrer", "content": "origin"},
        {"name": "color-scheme", "content": "dark"},
        {"name": "theme-color", "content": surface_color},
    ]
    head_links = [{"rel": "stylesheet", "href": href} for href in stylesheet_urls[:2]]
    head_links.append({"rel": "icon", "href": "./favicon.ico"})
    head_scripts = [
        {"slot": "head-config", "content": json.dumps({"title": summary.get("title"), "platform": summary.get("platform")}, ensure_ascii=False)},
        {"slot": "head-assets", "content": json.dumps({"scripts": asset_manifest.get("scripts", [])[:4], "stylesheets": stylesheet_urls[:3]}, ensure_ascii=False)},
        {"slot": "head-flags", "content": json.dumps({"signals": signal_bits[:4], "candidates": summary.get("candidate_count", 0)}, ensure_ascii=False)},
        {"slot": "head-css", "content": json.dumps({"stylesheets": css_analysis.get("stylesheet_count", 0), "inline": css_analysis.get("inline_style_tag_count", 0)}, ensure_ascii=False)},
        {"slot": "head-fonts", "content": json.dumps({"fonts": ((asset_manifest.get("fonts", {}) or {}).get("families", [])[:4])}, ensure_ascii=False)},
    ]
    body_scripts = [
        {"slot": "body-runtime-0", "content": json.dumps({"kind": "navigation", "text": nav_text}, ensure_ascii=False)},
        {"slot": "body-runtime-1", "content": json.dumps({"kind": "search", "text": search_text}, ensure_ascii=False)},
        {"slot": "body-runtime-2", "content": json.dumps({"kind": "footer", "text": footer_text}, ensure_ascii=False)},
        {"slot": "body-runtime-3", "content": json.dumps({"kind": "dialog", "text": dialog_text}, ensure_ascii=False)},
    ]
    body_styles = [
        ".bounded-runtime-copy{display:block;color:inherit;font:inherit;line-height:inherit;letter-spacing:inherit;text-align:inherit;text-transform:inherit;white-space:normal;}",
        f".bounded-runtime-shim--surface{{background:{focus_surface};border-radius:26px;border:1px solid rgba(0,0,0,0);}}",
    ]
    signature_shims = _build_reference_signature_shims(style_entries)
    if not signature_shims:
        signature_shims = [
            {"tag": "header", "className": "bounded-runtime-shim bounded-runtime-ref", "text": nav_text[:48]},
            {"tag": "a", "className": "bounded-runtime-shim bounded-runtime-ref", "text": "Gmail"},
            {"tag": "span", "className": "bounded-runtime-shim bounded-runtime-ref", "text": str(summary.get("title") or "Google")},
            {"tag": "g-popup", "className": "bounded-runtime-popup", "text": popup_text[:48]},
        ]
    return {
        "headMeta": head_meta,
        "headLinks": head_links,
        "headScripts": head_scripts,
        "bodyScripts": body_scripts,
        "bodyStyles": body_styles,
        "signatureShims": signature_shims,
        "navText": nav_text,
        "searchText": search_text,
        "footerText": footer_text,
        "dialogText": dialog_text,
        "popupText": popup_text,
        "surfaceColor": surface_color,
        "focusSurface": focus_surface,
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
            "function styleFromSnapshot(snapshot?: Record<string, unknown> | null, visualOnly = false): CSSProperties | undefined {",
            "  if (!snapshot || typeof snapshot !== \"object\") {",
            "    return undefined;",
            "  }",
            "  const style: CSSProperties = {};",
            "  const set = (key: keyof CSSProperties, value: unknown) => {",
            "    if (typeof value === \"string\" && value.trim()) {",
            "      style[key] = value as CSSProperties[keyof CSSProperties];",
            "    }",
            "  };",
            "  const allow = (field: string) => !visualOnly || [",
            '    "color",',
            '    "backgroundColor",',
            '    "backgroundImage",',
            '    "backgroundSize",',
            '    "backgroundPosition",',
            '    "backgroundRepeat",',
            '    "backgroundClip",',
            '    "fontFamily",',
            '    "fontSize",',
            '    "fontWeight",',
            '    "lineHeight",',
            '    "letterSpacing",',
            '    "textAlign",',
            '    "textTransform",',
            '    "whiteSpace",',
            '    "boxShadow",',
            '    "borderRadius",',
            '    "borderTopLeftRadius",',
            '    "borderTopRightRadius",',
            '    "borderBottomRightRadius",',
            '    "borderBottomLeftRadius",',
            '    "borderColor",',
            '    "borderStyle",',
            '    "borderWidth",',
            '    "opacity",',
            "  ].includes(field);",
            '  if (allow("display")) set("display", snapshot.display);',
            '  if (allow("position")) set("position", snapshot.position);',
            '  if (allow("width")) set("width", snapshot.width);',
            '  if (allow("height")) set("height", snapshot.height);',
            '  if (allow("minWidth")) set("minWidth", snapshot.minWidth);',
            '  if (allow("minHeight")) set("minHeight", snapshot.minHeight);',
            '  if (allow("maxWidth")) set("maxWidth", snapshot.maxWidth);',
            '  if (allow("maxHeight")) set("maxHeight", snapshot.maxHeight);',
            '  if (allow("marginTop")) set("marginTop", snapshot.marginTop);',
            '  if (allow("marginRight")) set("marginRight", snapshot.marginRight);',
            '  if (allow("marginBottom")) set("marginBottom", snapshot.marginBottom);',
            '  if (allow("marginLeft")) set("marginLeft", snapshot.marginLeft);',
            '  if (allow("paddingTop")) set("paddingTop", snapshot.paddingTop);',
            '  if (allow("paddingRight")) set("paddingRight", snapshot.paddingRight);',
            '  if (allow("paddingBottom")) set("paddingBottom", snapshot.paddingBottom);',
            '  if (allow("paddingLeft")) set("paddingLeft", snapshot.paddingLeft);',
            '  if (allow("overflow")) set("overflow", snapshot.overflow);',
            '  if (allow("overflowX")) set("overflowX", snapshot.overflowX);',
            '  if (allow("overflowY")) set("overflowY", snapshot.overflowY);',
            '  if (allow("boxSizing")) set("boxSizing", snapshot.boxSizing);',
            '  if (allow("zIndex")) set("zIndex", snapshot.zIndex);',
            '  if (allow("transform")) set("transform", snapshot.transform);',
            '  if (allow("transformOrigin")) set("transformOrigin", snapshot.transformOrigin);',
            '  if (allow("color")) set("color", snapshot.color);',
            '  if (allow("backgroundColor")) set("backgroundColor", snapshot.backgroundColor);',
            '  if (allow("backgroundImage")) set("backgroundImage", snapshot.backgroundImage);',
            '  if (allow("backgroundSize")) set("backgroundSize", snapshot.backgroundSize);',
            '  if (allow("backgroundPosition")) set("backgroundPosition", snapshot.backgroundPosition);',
            '  if (allow("backgroundRepeat")) set("backgroundRepeat", snapshot.backgroundRepeat);',
            '  if (allow("backgroundClip")) set("backgroundClip", snapshot.backgroundClip);',
            '  if (allow("fontFamily")) set("fontFamily", snapshot.fontFamily);',
            '  if (allow("fontSize")) set("fontSize", snapshot.fontSize);',
            '  if (allow("fontWeight")) set("fontWeight", snapshot.fontWeight);',
            '  if (allow("lineHeight")) set("lineHeight", snapshot.lineHeight);',
            '  if (allow("letterSpacing")) set("letterSpacing", snapshot.letterSpacing);',
            '  if (allow("textAlign")) set("textAlign", snapshot.textAlign);',
            '  if (allow("textTransform")) set("textTransform", snapshot.textTransform);',
            '  if (allow("whiteSpace")) set("whiteSpace", snapshot.whiteSpace);',
            '  if (allow("boxShadow")) set("boxShadow", snapshot.boxShadow);',
            '  if (allow("borderRadius")) set("borderRadius", snapshot.borderRadius);',
            '  if (allow("borderTopLeftRadius")) set("borderTopLeftRadius", snapshot.borderTopLeftRadius);',
            '  if (allow("borderTopRightRadius")) set("borderTopRightRadius", snapshot.borderTopRightRadius);',
            '  if (allow("borderBottomRightRadius")) set("borderBottomRightRadius", snapshot.borderBottomRightRadius);',
            '  if (allow("borderBottomLeftRadius")) set("borderBottomLeftRadius", snapshot.borderBottomLeftRadius);',
            '  if (allow("borderColor")) set("borderColor", snapshot.borderColor);',
            '  if (allow("borderStyle")) set("borderStyle", snapshot.borderStyle);',
            '  if (allow("borderWidth")) set("borderWidth", snapshot.borderWidth);',
            '  if (allow("gap")) set("gap", snapshot.gap);',
            '  if (allow("flexWrap")) set("flexWrap", snapshot.flexWrap);',
            '  if (allow("alignContent")) set("alignContent", snapshot.alignContent);',
            '  if (allow("justifyContent")) set("justifyContent", snapshot.justifyContent);',
            '  if (allow("alignItems")) set("alignItems", snapshot.alignItems);',
            '  if (allow("flexDirection")) set("flexDirection", snapshot.flexDirection);',
            '  if (allow("opacity")) set("opacity", snapshot.opacity);',
            "  return Object.keys(style).length ? style : undefined;",
            "}",
            "",
            "function stageRectStyle(",
            "  rect: { x?: number | null; y?: number | null; width?: number | null; height?: number | null } | null | undefined,",
            "  viewport: { width?: number | null; height?: number | null } | null | undefined,",
            "): CSSProperties | undefined {",
            "  if (!rect || !viewport || !viewport.width || !viewport.height) {",
            "    return undefined;",
            "  }",
            "  const width = Number(viewport.width) || 1;",
            "  const height = Number(viewport.height) || 1;",
            "  return {",
            "    left: `${((Number(rect.x) || 0) / width) * 100}%`,",
            "    top: `${((Number(rect.y) || 0) / height) * 100}%`,",
            "    width: `${((Number(rect.width) || 0) / width) * 100}%`,",
            "    minHeight: `${Math.max(((Number(rect.height) || 0) / height) * 100, 3)}%`,",
            "  };",
            "}",
            "",
            "export function BoundedReferencePage({ data }: Props) {",
            "  const mastheadStyle = styleFromSnapshot(data.masthead.styleSnapshot, true);",
            "  const heroStyle = styleFromSnapshot(data.hero.styleSnapshot, true);",
            "  const focusShellStyle = styleFromSnapshot(data.hero.focusShellStyle, true);",
            "  const focusInputStyle = styleFromSnapshot(data.hero.focusInput?.styleSnapshot);",
            '  const centeredFocus = data.layoutMode === "centered-focus";',
            '  const appShellMode = data.layoutMode === "app-shell";',
            '  const shellPanels = data.shellPanels ?? [];',
            "  const renderFocusInput = () => {",
            '    const placeholder = data.hero.focusInput?.placeholder ?? data.hero.focusInput?.label ?? data.title;',
            '    const inputType = data.hero.focusInput?.inputType ?? "text";',
            '    if (data.hero.focusInput?.controlTag === "textarea") {',
            '      return <textarea aria-autocomplete="list" aria-expanded="false" aria-haspopup="listbox" aria-label={placeholder} className="bounded-focus-input" defaultValue="" placeholder={placeholder} role={data.hero.focusInput?.role ?? "combobox"} style={focusInputStyle} />;',
            "    }",
            '    if (data.hero.focusInput?.controlTag === "select") {',
            '      return <select className="bounded-focus-input" defaultValue="sample" style={focusInputStyle}><option value="sample">{placeholder}</option></select>;',
            "    }",
            '    return <input aria-label={placeholder} className="bounded-focus-input" defaultValue="" placeholder={placeholder} role={data.hero.focusInput?.role} type={inputType} style={focusInputStyle} />;',
            "  };",
            "  const renderAction = (action: (typeof data.hero.actions)[number]) => {",
            "    const actionStyle = styleFromSnapshot(action.styleSnapshot);",
            "    if (action.href || action.controlTag === \"a\") {",
            '      return <a className={`bounded-focus-button${/google apps?|google 앱/i.test(action.label) ? " bounded-focus-button--apps" : ""}`} href={action.href ?? "#"} key={`${action.label}-${action.href ?? "inline"}`} role={action.role ?? (/google apps?|google 앱/i.test(action.label) ? "button" : undefined)} style={actionStyle}>{action.label}</a>;',
            "    }",
            '    return <input aria-label={action.label} className=\"bounded-focus-button bounded-focus-button--input\" key={`${action.label}-${action.href ?? \"inline\"}`} name={action.label} role={action.role} style={actionStyle} title={action.label} type={/google 검색|search|i.?m feeling lucky/i.test(action.label) ? \"submit\" : (action.inputType ?? \"button\")} value={action.label} />;',
            "  };",
            "  const focusActions = centeredFocus",
            "    ? (data.hero.actions.some((action) => /google 검색|search|i.?m feeling lucky|lucky/i.test(action.label ?? \"\"))",
            "        ? data.hero.actions",
            '        : [{ label: "Google 검색", controlTag: "input", inputType: "submit" }, { label: "I’m Feeling Lucky", controlTag: "input", inputType: "submit" }])',
            "    : data.hero.actions;",
            "  const navVisibleLabel = (label?: string | null) => {",
            '    if (!label) return "";',
            '    if (/이미지 검색/i.test(label)) return "이미지";',
            '    if (/google apps?|google 앱/i.test(label)) return "";',
            "    return label;",
            "  };",
            "  const navAriaLabel = (label?: string | null) => {",
            '    if (!label) return undefined;',
            '    if (/google apps?|google 앱|이미지 검색|gmail|로그인|login/i.test(label)) return label;',
            "    return undefined;",
            "  };",
            "  const renderNavLink = (link: (typeof data.masthead.links)[number], className = \"bounded-nav-link\") => {",
            "    const visibleLabel = navVisibleLabel(link.label);",
            "    const ariaLabel = navAriaLabel(link.label);",
            "    return (",
            '      <a aria-label={ariaLabel} className={`${className}${/google apps?|google 앱/i.test(link.label ?? "") ? " bounded-nav-link--apps" : ""}`} href={link.href ?? "#"} key={`${link.label}-${link.href ?? "inline"}`} role={link.role ?? (/google apps?|google 앱/i.test(link.label ?? "") ? "button" : undefined)} style={styleFromSnapshot(link.styleSnapshot) ?? mastheadStyle}>',
            "        {visibleLabel || null}",
            "      </a>",
            "    );",
            "  };",
            "  const focusAuxGlyph = (label: string | null | undefined, index: number) => {",
            '    if (/음성 검색/i.test(label ?? "")) return "◎";',
            '    if (/이미지로 검색|lens/i.test(label ?? "")) return "◌";',
            '    if (/입력 도구/i.test(label ?? "")) return "⌨";',
            '    return index === 0 ? "◎" : "•";',
            "  };",
            "  const renderWordmark = () => (",
            '    <div className="bounded-logo-shell">',
            '      <svg aria-label={data.hero.title} className="bounded-logo-mark" role="img" viewBox="0 0 272 92" xmlns="http://www.w3.org/2000/svg">',
            '        <text className="bounded-logo-wordmark" x="0" y="68">',
            '          <tspan fill="rgb(66, 133, 244)">G</tspan>',
            '          <tspan fill="rgb(234, 67, 53)">o</tspan>',
            '          <tspan fill="rgb(251, 188, 5)">o</tspan>',
            '          <tspan fill="rgb(66, 133, 244)">g</tspan>',
            '          <tspan fill="rgb(52, 168, 83)">l</tspan>',
            '          <tspan fill="rgb(234, 67, 53)">e</tspan>',
            "        </text>",
            "      </svg>",
            "    </div>",
            "  );",
            "  const renderInteractionControl = (entry: (typeof data.interactions)[number]) => {",
            "    const controlStyle = styleFromSnapshot(entry.styleSnapshot);",
            "    if (entry.controlTag === \"a\") {",
            '      return <a className={`bounded-control bounded-control--link${/google apps?|google 앱/i.test(entry.label) ? " bounded-control--link--apps" : ""}`} href={entry.href ?? "#"} role={entry.role ?? (/google apps?|google 앱/i.test(entry.label) ? "button" : undefined)} style={controlStyle}>{entry.label}</a>;',
            "    }",
            "    if (entry.controlTag === \"textarea\") {",
            '      return <textarea aria-autocomplete="list" aria-expanded="false" aria-haspopup="listbox" aria-label={entry.label} className=\"bounded-control bounded-control--input\" defaultValue={entry.copy} placeholder={entry.placeholder ?? entry.label} role={entry.role ?? "combobox"} style={controlStyle} />;',
            "    }",
            "    if (entry.controlTag === \"input\") {",
            '      return <input aria-label={entry.label} className=\"bounded-control bounded-control--input\" defaultValue={entry.kind === \"text-entry\" ? entry.copy : undefined} placeholder={entry.placeholder ?? entry.label} role={entry.role} type={/google 검색|search|i.?m feeling lucky/i.test(entry.label) ? \"submit\" : (entry.inputType ?? \"text\")} style={controlStyle} />;',
            "    }",
            "    if (entry.controlTag === \"select\") {",
            '      return <select className=\"bounded-control bounded-control--input\" defaultValue=\"sample\" style={controlStyle}><option value=\"sample\">{entry.label}</option><option value=\"alt\">Captured option</option></select>;',
            "    }",
            '    return <button className=\"bounded-control bounded-control--button\" type=\"button\" style={controlStyle}>{entry.label}</button>;',
            "  };",
            "  const renderRuntimeShim = (entry: NonNullable<typeof data.runtimeMaterialization>['signatureShims'][number], index: number) => {",
            "    const key = `${entry.tag}-${entry.className}-${index}`;",
            "    const role = entry.role ?? undefined;",
            "    const shimStyle = styleFromSnapshot(entry.styleSnapshot);",
            "    const content = <span className=\"bounded-runtime-copy\">{entry.text}</span>;",
            '    const shimProps = { "aria-hidden": true, "data-web-embedding-ignore-interactions": "true", tabIndex: -1 } as const;',
            "    if (entry.tag === \"form\") {",
            "      return <form {...shimProps} className={entry.className} key={key} role={role} style={shimStyle}>{content}</form>;",
            "    }",
            "    if (entry.tag === \"g-popup\") {",
            "      return <g-popup {...shimProps} className={entry.className} key={key} style={shimStyle}>{entry.text}</g-popup>;",
            "    }",
            "    if (entry.tag === \"a\") {",
            "      return <a {...shimProps} className={entry.className} href=\"#\" key={key} role={role} style={shimStyle}>{entry.text}</a>;",
            "    }",
            "    if (entry.tag === \"input\") {",
            "      return <input {...shimProps} aria-label={entry.text} className={entry.className} key={key} role={role} style={shimStyle} title={entry.text} type=\"button\" value={entry.text} />;",
            "    }",
            "    if (entry.tag === \"textarea\") {",
            "      return <textarea {...shimProps} aria-label={entry.text} className={entry.className} defaultValue={entry.text} key={key} role={role} style={shimStyle} />;",
            "    }",
            "    if (entry.tag === \"span\") {",
            "      return <span {...shimProps} className={entry.className} key={key} role={role} style={shimStyle}>{entry.text}</span>;",
            "    }",
            "    if (entry.tag === \"center\") {",
            "      return <center {...shimProps} className={entry.className} key={key} role={role} style={shimStyle}>{content}</center>;",
            "    }",
            "    if (entry.tag === \"svg\") {",
            "      return <svg aria-hidden=\"true\" className={entry.className} data-web-embedding-ignore-interactions=\"true\" focusable=\"false\" key={key} role={role ?? \"img\"} style={shimStyle} viewBox=\"0 0 24 24\"><path d=\"M4 12h16M12 4v16\" fill=\"none\" stroke=\"currentColor\" strokeWidth=\"1.5\" /></svg>;",
            "    }",
            "    if (entry.tag === \"path\") {",
            "      return <svg aria-hidden=\"true\" className={entry.className} data-web-embedding-ignore-interactions=\"true\" focusable=\"false\" key={key} style={{ display: \"inline\" }} viewBox=\"0 0 24 24\"><path d=\"M4 12h16M12 4v16\" style={shimStyle} /></svg>;",
            "    }",
            "    if (entry.tag === \"header\") {",
            "      return <header {...shimProps} className={entry.className} key={key} role={role} style={shimStyle}>{content}</header>;",
            "    }",
            "    return <div {...shimProps} className={entry.className} key={key} role={role} style={shimStyle}>{content}</div>;",
            "  };",
            "  return (",
            '    <div className="bounded-shell">',
            '      <header className={`bounded-masthead bounded-panel${centeredFocus ? " bounded-masthead--minimal" : ""}`} style={mastheadStyle}>',
            '        {!centeredFocus ? (',
            '          <div className="bounded-brand-block">',
            '            <p className="bounded-eyebrow" style={mastheadStyle}>Captured reference</p>',
            '            <strong className="bounded-brand" style={mastheadStyle}>{data.masthead.brand}</strong>',
            "          </div>",
            "        ) : null}",
            '        <div className={`bounded-nav${centeredFocus ? " bounded-nav--split" : ""}`} role="navigation" style={mastheadStyle}>',
            "          {centeredFocus ? (",
            "            <>",
            '              <div className="bounded-nav-cluster">',
            "                {data.masthead.leadingLinks.map((link) => renderNavLink(link))}",
            "              </div>",
            '              <div className="bounded-nav-cluster bounded-nav-cluster--end">',
            "                {data.masthead.trailingLinks.map((link) => renderNavLink(link, `bounded-nav-link${/로그인|login/i.test(link.label ?? \"\") ? \" bounded-nav-link--cta\" : \"\"}`))}",
            "              </div>",
            "            </>",
            "          ) : data.masthead.links.length ? (",
            "            data.masthead.links.map((link) => renderNavLink(link))",
            "          ) : (",
            '            <span className="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>',
            "          )}",
            "        </div>",
            "      </header>",
            "",
            '      <div className={`bounded-hero bounded-panel${centeredFocus ? " bounded-hero--centered bounded-hero--focus" : ""}`} style={heroStyle}>',
            '        {!centeredFocus ? <p className="bounded-eyebrow" style={heroStyle}>{data.hero.eyebrow}</p> : null}',
            '        {centeredFocus ? renderWordmark() : <h1 style={heroStyle}>{data.hero.title}</h1>}',
            '        {!centeredFocus && data.hero.copy ? <p className="bounded-lede" style={heroStyle}>{data.hero.copy}</p> : null}',
            '        {centeredFocus ? (',
            '          <>',
            '            <form className="bounded-focus-form" role="search">',
            '              <div className="bounded-focus-shell-frame">',
            '                <div className="bounded-focus-shell" style={focusShellStyle}>',
            '                  <span className="bounded-focus-icon" aria-hidden="true">⌕</span>',
            "                  {renderFocusInput()}",
            '                  <div className="bounded-focus-aux">',
            "                    {(data.hero.focusAuxiliary?.length ? data.hero.focusAuxiliary : [{ label: \"음성 검색\" }, { label: \"이미지로 검색\" }]).slice(0, 3).map((entry, index) => (",
                    '                      <div className="bounded-focus-aux-button bounded-focus-icon" data-glyph={focusAuxGlyph(entry.label, index)} key={`${entry.label}-${index}`} role={entry.role ?? "button"} style={styleFromSnapshot(entry.styleSnapshot)} tabIndex={0}><span className="bounded-sr-only">{entry.label}</span></div>',
            "                    ))}",
            "                  </div>",
            "                </div>",
            "              </div>",
            '              <div className="bounded-focus-actions-row">',
            '                <div className="bounded-focus-actions">',
            '                  {(focusActions.length ? focusActions : [{ label: "Search", href: "#" }, { label: "Explore", href: "#" }]).slice(0, 2).map((action) => renderAction(action))}',
            "                </div>",
            "              </div>",
            '            </form>',
            '          </>',
            '        ) : (',
            '          <>',
            '            <div className="bounded-meta">',
            "              {data.metaBits.map((bit) => (",
            '                <span className="bounded-chip" key={bit} style={heroStyle}>',
            "                  {bit}",
            "                </span>",
            "              ))}",
            "            </div>",
            '            <div className="bounded-hero-actions">',
            '              <span className="bounded-chip bounded-chip--muted" style={heroStyle}>{data.hero.meta}</span>',
            "              {data.hero.details.slice(0, 3).map((detail) => (",
            '                <span className="bounded-chip bounded-chip--muted" key={detail} style={heroStyle}>',
            "                  {detail}",
            "                </span>",
            "              ))}",
            "              {data.hero.actions.map((action) => (",
            '                <a className="bounded-cta" href={action.href ?? "#"} key={`${action.label}-${action.href ?? "inline"}`} style={styleFromSnapshot(action.styleSnapshot)}>',
            "                  {action.label}",
            "                </a>",
            "              ))}",
            "            </div>",
            "          </>",
            "        )}",
            "      </div>",
            "",
            '      {appShellMode ? (',
            '        <div className="bounded-panel bounded-stack bounded-app-shell">',
            '          <p className="bounded-kicker">App-shell surface</p>',
            '          <div style={{ display: "grid", gap: "16px", gridTemplateColumns: "minmax(200px, 240px) minmax(0, 1fr) minmax(220px, 280px)" }}>',
            "            {shellPanels.map((panel) => (",
            '              <div className="bounded-panel bounded-stack" key={panel.id}>',
            '                <p className="bounded-kicker">{panel.title}</p>',
            "                {(panel.items ?? []).slice(0, 6).map((item, index) => (",
            '                  <div className="bounded-mini-card" key={`${panel.id}-${item.label ?? item.copy ?? index}`} style={styleFromSnapshot(item.styleSnapshot)}>',
            '                    <strong>{item.label ?? item.kind ?? "panel item"}</strong>',
            '                    {item.copy ? <p>{item.copy}</p> : null}',
            '                    {item.meta ? <span className="bounded-outline-meta">{item.meta}</span> : null}',
            '                    {item.states?.length ? (',
            '                      <div className="bounded-meta bounded-meta--inline">',
            "                        {item.states.slice(0, 3).map((state) => (",
            '                          <span className="bounded-chip bounded-chip--muted" key={state}>',
            "                            {state}",
            "                          </span>",
            "                        ))}",
            "                      </div>",
            "                    ) : null}",
            "                  </div>",
            "                ))}",
            "              </div>",
            "            ))}",
            "          </div>",
            "        </div>",
            "      ) : null}",
            "",
            '      <div className={`bounded-stage bounded-panel${centeredFocus ? " bounded-stage--compact" : ""}`}>',
            '            <div className="bounded-stage-canvas">',
            "              {data.sections.slice(0, 8).map((section) => (",
            '                <div',
            '                  className="bounded-stage-block bounded-panel"',
            "                  data-role={section.role}",
            "                  key={`stage-${section.id}`}",
            "                  style={{ ...stageRectStyle(section.rect, data.viewport), ...styleFromSnapshot(section.styleSnapshot) }}",
            "                >",
            '                  <p className="bounded-kicker">{section.role}</p>',
            "                  <strong>{section.title}</strong>",
            "                  <p className=\"bounded-copy\">{section.copy}</p>",
            "                </div>",
            "              ))}",
            "        </div>",
            "      </div>",
            "",
            '      <div className={`bounded-layout${centeredFocus ? " bounded-layout--centered" : ""}`}>',
            '        <div className="bounded-main">',
            '          <div className="bounded-section-grid">',
            "                {data.bodySections.map((section) => (",
            '                  <div className="bounded-card bounded-panel" data-role={section.role} key={section.id} style={styleFromSnapshot(section.styleSnapshot)}>',
            '                    <div className="bounded-card-head">',
            '                      <p className="bounded-kicker" style={styleFromSnapshot(section.styleSnapshot)}>{section.role}</p>',
            '                      <span className="bounded-chip bounded-chip--muted" style={styleFromSnapshot(section.styleSnapshot)}>{section.tag}</span>',
            "                    </div>",
            '                    <h2 style={styleFromSnapshot(section.styleSnapshot)}>{section.title}</h2>',
            '                    <p className="bounded-copy" style={styleFromSnapshot(section.styleSnapshot)}>{section.copy}</p>',
            '                    <div className="bounded-meta bounded-meta--inline">',
            '                      <span className="bounded-chip" style={styleFromSnapshot(section.styleSnapshot)}>{section.meta}</span>',
            "                      {section.details.slice(0, 3).map((detail) => (",
            '                        <span className="bounded-chip bounded-chip--muted" key={detail} style={styleFromSnapshot(section.styleSnapshot)}>',
            "                          {detail}",
            "                        </span>",
            "                      ))}",
            "                    </div>",
            "                  </div>",
            "                ))}",
            "          </div>",
            "          {!centeredFocus ? (",
            '            <div className="bounded-panel bounded-stack bounded-visible-interactions">',
            '              <p className="bounded-kicker">Interaction samples</p>',
            '              <div className="bounded-stack bounded-control-grid">',
            "                {data.interactions.length ? (",
            "                  data.interactions.map((entry) => (",
            '                    <div className="bounded-mini-card" key={entry.id}>',
            '                      <strong>{entry.label}</strong>',
            '                      <p>{entry.copy}</p>',
            "                      {renderInteractionControl(entry)}",
            '                      <div className="bounded-meta bounded-meta--inline">',
            "                        {entry.states.slice(0, 3).map((state) => (",
            '                          <span className="bounded-chip bounded-chip--muted" key={state}>',
            "                            {state}",
            "                          </span>",
            "                        ))}",
            "                      </div>",
            "                    </div>",
            "                  ))",
            "                ) : (",
            '                  <div className="bounded-mini-card">',
            "                    <strong>No sampled interactions</strong>",
            "                    <p>Interaction data was not available in the capture bundle.</p>",
            "                  </div>",
            "                )}",
            "              </div>",
            "            </div>",
            "          ) : null}",
            "        </div>",
            "",
            '        <div className="bounded-rail bounded-telemetry" aria-hidden="true">',
            '              <div className="bounded-panel bounded-stack">',
            '                <p className="bounded-kicker">Renderer status</p>',
            '                <div className="bounded-status-row">',
            "                  <strong>{data.reconstruction.strategy}</strong>",
            '                  <span className="bounded-chip">{data.reconstruction.confidence}</span>',
            "                </div>",
            '                <ul className="bounded-list">',
            "                  {data.reconstruction.remainingGaps.map((item) => (",
            "                    <li key={item}>{item}</li>",
            "                  ))}",
            "                </ul>",
            "              </div>",
            "",
            '              <div className="bounded-panel bounded-stack">',
            '                <p className="bounded-kicker">Signals</p>',
            '                <div className="bounded-meta bounded-meta--inline">',
            "                  {data.signalBits.length ? (",
            "                    data.signalBits.map((signal) => (",
            '                      <span className="bounded-chip bounded-chip--muted" key={signal}>',
            "                        {signal}",
            "                      </span>",
            "                    ))",
            "                  ) : (",
            '                    <span className="bounded-chip bounded-chip--muted">No extra runtime signals were captured.</span>',
            "                  )}",
            "                </div>",
            "              </div>",
            "",
            '              <div className="bounded-panel bounded-stack">',
            '                <p className="bounded-kicker">Layout rhythm</p>',
            '                <div className="bounded-stack bounded-stack--tight">',
            "                  {data.reconstruction.layoutRhythm.slice(0, 6).map((item) => (",
            '                    <div className="bounded-outline-item" key={item.id}>',
            '                      <strong>{item.role}</strong>',
            '                      <p>{item.size}</p>',
            '                      <span className="bounded-outline-meta">y: {item.y ?? 0}</span>',
            "                    </div>",
            "                  ))}",
            "                </div>",
            "              </div>",
            "            </div>",
            "      </div>",
            "",
            '      {(data.footer?.leftLinks?.length || data.footer?.rightLinks?.length || data.footer?.controls?.length) ? (',
            '        <div className="bounded-footer bounded-footer--frame" role="contentinfo" style={styleFromSnapshot(data.footer.styleSnapshot, true)}>',
            '          <div className="bounded-footer-cluster">',
            '            {(data.footer.leftLinks ?? []).map((link) => (',
            '              <a className="bounded-footer-link" href={link.href ?? "#"} key={`${link.label}-${link.href ?? "inline"}`} style={styleFromSnapshot(link.styleSnapshot)}>',
            "                {link.label}",
            "              </a>",
            "            ))}",
            "          </div>",
            '          <div className="bounded-footer-cluster bounded-footer-cluster--end">',
            '            {(data.footer.rightLinks ?? []).map((link) => (',
            '              <a className="bounded-footer-link" href={link.href ?? "#"} key={`${link.label}-${link.href ?? "inline"}`} style={styleFromSnapshot(link.styleSnapshot)}>',
            "                {link.label}",
            "              </a>",
            "            ))}",
            '            {(data.footer.controls ?? []).map((control, index) => (',
            '              <div className="bounded-footer-link bounded-footer-link--button" key={`${control.label}-${index}`} role="button" style={styleFromSnapshot(control.styleSnapshot)} tabIndex={0}>',
            "                {control.label}",
            "              </div>",
            "            ))}",
            "          </div>",
            "        </div>",
            "      ) : null}",
            "",
            '      <div aria-hidden="true" className="bounded-runtime-materialization" data-web-embedding-ignore-interactions="true">',
            '        {(data.runtimeMaterialization?.bodyStyles ?? []).map((sheet, index) => (',
            '          <style data-bounded-runtime-inline={index} dangerouslySetInnerHTML={{ __html: sheet }} key={`runtime-style-${index}`} />',
            "        ))}",
            '        {(data.runtimeMaterialization?.signatureShims ?? []).map((entry, index) => renderRuntimeShim(entry, index))}',
            '        <dialog aria-hidden="true" className="bounded-runtime-dialog" data-web-embedding-ignore-interactions="true"><span className="bounded-runtime-copy">{data.runtimeMaterialization?.dialogText ?? "runtime dialog"}</span></dialog>',
            '        <textarea aria-hidden="true" className="bounded-runtime-textarea" data-web-embedding-ignore-interactions="true" defaultValue={data.runtimeMaterialization?.searchText ?? "runtime textarea"} readOnly tabIndex={-1} />',
            '        <span aria-hidden="true" className="bounded-runtime-copy" data-web-embedding-ignore-interactions="true">{data.runtimeMaterialization?.navText ?? "runtime span"}</span>',
            '        <span aria-hidden="true" className="bounded-runtime-copy" data-web-embedding-ignore-interactions="true">{data.runtimeMaterialization?.footerText ?? "runtime span"}</span>',
            '        {(data.runtimeMaterialization?.bodyScripts ?? []).map((entry, index) => (',
            '          <script data-bounded-runtime={entry.slot ?? `runtime-body-${index}`} dangerouslySetInnerHTML={{ __html: entry.content ?? "{}" }} key={entry.slot ?? `runtime-body-${index}`} type="application/json" />',
            "        ))}",
            "      </div>",
            "    </div>",
            "  );",
            "}",
        ]
    )


def _render_bounded_reference_page_html(app_model: dict[str, Any]) -> str:
    masthead = app_model.get("masthead", {}) if isinstance(app_model, dict) else {}
    hero = app_model.get("hero", {}) if isinstance(app_model, dict) else {}
    reconstruction = app_model.get("reconstruction", {}) if isinstance(app_model, dict) else {}
    runtime_materialization = app_model.get("runtimeMaterialization", {}) if isinstance(app_model, dict) else {}
    meta_bits = app_model.get("metaBits", []) if isinstance(app_model.get("metaBits", []), list) else []
    signal_bits = app_model.get("signalBits", []) if isinstance(app_model.get("signalBits", []), list) else []
    body_sections = app_model.get("bodySections", []) if isinstance(app_model.get("bodySections", []), list) else []
    interactions = app_model.get("interactions", []) if isinstance(app_model.get("interactions", []), list) else []
    layout_rhythm = reconstruction.get("layoutRhythm", []) if isinstance(reconstruction.get("layoutRhythm", []), list) else []
    masthead_style = _style_attr_from_snapshot(masthead.get("styleSnapshot"), visual_only=True)
    hero_style = _style_attr_from_snapshot(hero.get("styleSnapshot"), visual_only=True)
    focus_shell_style = _style_attr_from_snapshot(hero.get("focusShellStyle"), visual_only=True)
    focus_input = hero.get("focusInput", {}) if isinstance(hero.get("focusInput", {}), dict) else {}
    focus_input_style = _style_attr_from_snapshot(focus_input.get("styleSnapshot"))
    focus_auxiliary = hero.get("focusAuxiliary", []) if isinstance(hero.get("focusAuxiliary", []), list) else []
    layout_mode = str(app_model.get("layoutMode") or "structured-grid")
    centered_focus = layout_mode == "centered-focus"
    app_shell_mode = layout_mode == "app-shell"
    shell_panels = app_model.get("shellPanels", []) if isinstance(app_model.get("shellPanels", []), list) else []
    viewport = app_model.get("viewport", {}) if isinstance(app_model, dict) else {}
    viewport_width = max(int(viewport.get("width") or 1440), 1)
    viewport_height = max(int(viewport.get("height") or 1200), 1)
    nav_shadow_text = escape(str(runtime_materialization.get("navText") or ""))
    search_shadow_text = escape(str(runtime_materialization.get("searchText") or ""))
    footer_shadow_text = escape(str(runtime_materialization.get("footerText") or ""))
    dialog_shadow_text = escape(str(runtime_materialization.get("dialogText") or ""))
    popup_shadow_text = escape(str(runtime_materialization.get("popupText") or ""))
    head_meta_bits = [
        f'<meta content="{escape(str(entry.get("content") or ""))}" name="{escape(str(entry.get("name") or "runtime-placeholder"))}" />'
        for entry in (runtime_materialization.get("headMeta", []) if isinstance(runtime_materialization.get("headMeta", []), list) else [])
        if isinstance(entry, dict)
    ]
    head_link_bits = [
        f'<link href="{escape(str(entry.get("href") or "#"))}" rel="{escape(str(entry.get("rel") or "stylesheet"))}" />'
        for entry in (runtime_materialization.get("headLinks", []) if isinstance(runtime_materialization.get("headLinks", []), list) else [])
        if isinstance(entry, dict)
    ]
    head_script_bits = [
        f'<script data-bounded-runtime="{escape(str(entry.get("slot") or f"head-{index}"))}" type="application/json">{escape(str(entry.get("content") or "{}"))}</script>'
        for index, entry in enumerate(runtime_materialization.get("headScripts", []) if isinstance(runtime_materialization.get("headScripts", []), list) else [])
        if isinstance(entry, dict)
    ]
    body_style_bits = [
        f'<style data-bounded-runtime-inline="{index}">{escape(str(sheet))}</style>'
        for index, sheet in enumerate(runtime_materialization.get("bodyStyles", []) if isinstance(runtime_materialization.get("bodyStyles", []), list) else [])
        if str(sheet).strip()
    ]
    body_script_bits = [
        f'<script data-bounded-runtime="{escape(str(entry.get("slot") or f"body-{index}"))}" type="application/json">{escape(str(entry.get("content") or "{}"))}</script>'
        for index, entry in enumerate(runtime_materialization.get("bodyScripts", []) if isinstance(runtime_materialization.get("bodyScripts", []), list) else [])
        if isinstance(entry, dict)
    ]

    def render_runtime_shim_html(entry: dict[str, Any]) -> str:
        tag = escape(str(entry.get("tag") or "div"))
        class_attr = escape(str(entry.get("className") or "bounded-runtime-shim"))
        hidden_attr = ' aria-hidden="true" data-web-embedding-ignore-interactions="true"'
        role_attr = f' role="{escape(str(entry.get("role") or ""))}"' if entry.get("role") else ""
        href_attr = ' href="#"' if str(entry.get("tag") or "").lower() == "a" else ""
        tabindex_attr = ' tabindex="-1"'
        style_attr = _style_attr_from_snapshot(entry.get("styleSnapshot"))
        text_value = escape(str(entry.get("text") or ""))
        lowered_tag = str(entry.get("tag") or "").lower()
        if lowered_tag == "input":
            return f'<input aria-label="{text_value}" class="{class_attr}"{hidden_attr}{role_attr}{tabindex_attr}{style_attr} title="{text_value}" type="button" value="{text_value}" />'
        if lowered_tag == "textarea":
            return f'<textarea aria-label="{text_value}" class="{class_attr}"{hidden_attr}{role_attr}{tabindex_attr}{style_attr}>{text_value}</textarea>'
        if lowered_tag == "svg":
            svg_role = role_attr or ' role="img"'
            return f'<svg aria-hidden="true" class="{class_attr}" data-web-embedding-ignore-interactions="true" focusable="false"{svg_role}{style_attr} viewBox="0 0 24 24"><path d="M4 12h16M12 4v16" fill="none" stroke="currentColor" stroke-width="1.5"></path></svg>'
        if lowered_tag == "path":
            return f'<svg aria-hidden="true" class="{class_attr}" data-web-embedding-ignore-interactions="true" focusable="false" viewBox="0 0 24 24" style="display:inline;"><path d="M4 12h16M12 4v16"{style_attr}></path></svg>'
        if str(entry.get("tag") or "").lower() in {"span", "a", "g-popup"}:
            return f'<{tag} class="{class_attr}"{hidden_attr}{href_attr}{role_attr}{tabindex_attr}{style_attr}>{text_value}</{tag}>'
        return f'<{tag} class="{class_attr}"{hidden_attr}{href_attr}{role_attr}{tabindex_attr}{style_attr}><span class="bounded-runtime-copy">{text_value}</span></{tag}>'

    signature_shim_bits = [
        render_runtime_shim_html(entry)
        for entry in (runtime_materialization.get("signatureShims", []) if isinstance(runtime_materialization.get("signatureShims", []), list) else [])
        if isinstance(entry, dict)
    ]
    head_markup = (
        '<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />'
        f'<title>{escape(str(app_model.get("title") or "Captured reference"))}</title>'
        + "".join(head_meta_bits)
        + "".join(head_link_bits)
        + "".join(head_script_bits)
        + '<link rel="stylesheet" href="./next-app/app/fonts.css" />'
        + '<link rel="stylesheet" href="./next-app/app/globals.css" />'
        + "</head>"
    )
    runtime_materialization_markup = (
        '<div class="bounded-runtime-materialization" aria-hidden="true" data-web-embedding-ignore-interactions="true">'
        + "".join(body_style_bits)
        + "".join(signature_shim_bits)
        + f'<dialog aria-hidden="true" class="bounded-runtime-dialog" data-web-embedding-ignore-interactions="true"><span class="bounded-runtime-copy">{dialog_shadow_text or "runtime dialog"}</span></dialog>'
        + f'<textarea aria-hidden="true" class="bounded-runtime-textarea" data-web-embedding-ignore-interactions="true" readonly tabindex="-1">{search_shadow_text or "runtime textarea"}</textarea>'
        + f'<span aria-hidden="true" class="bounded-runtime-copy" data-web-embedding-ignore-interactions="true">{nav_shadow_text or "runtime span"}</span>'
        + f'<span aria-hidden="true" class="bounded-runtime-copy" data-web-embedding-ignore-interactions="true">{footer_shadow_text or "runtime span"}</span>'
        + "".join(body_script_bits)
        + "</div>"
    )

    def render_interaction_control(entry: dict[str, Any]) -> str:
        control_style = _style_attr_from_snapshot(entry.get("styleSnapshot"))
        label = escape(str(entry.get("label") or "Captured interaction"))
        placeholder = escape(str(entry.get("placeholder") or entry.get("label") or "Captured input"))
        input_type = escape(str(entry.get("inputType") or "text"))
        href = escape(str(entry.get("href") or "#"))
        tag = str(entry.get("controlTag") or "button")
        kind = str(entry.get("kind") or "")
        if tag == "a":
            extra_class = " bounded-control--link--apps" if _is_google_apps_label(label) else ""
            role_attr = ' role="button"' if _is_google_apps_label(label) else ""
            return f'                  <a class="bounded-control bounded-control--link{extra_class}" href="{href}"{role_attr}{control_style}>{label}</a>'
        if tag == "textarea":
            value = escape(str(entry.get("copy") or ""))
            return f'                  <textarea aria-autocomplete="list" aria-expanded="false" aria-haspopup="listbox" class="bounded-control bounded-control--input" placeholder="{placeholder}" role="combobox"{control_style}>{value}</textarea>'
        if tag == "input":
            value = escape(str(entry.get("copy") or "")) if kind == "text-entry" else ""
            submit_type = "submit" if _is_google_submit_label(label) else input_type
            return f'                  <input aria-label="{label}" class="bounded-control bounded-control--input" type="{submit_type}" value="{value}" placeholder="{placeholder}"{control_style} />'
        if tag == "select":
            return "\n".join(
                [
                    f'                  <select class="bounded-control bounded-control--input"{control_style}>',
                    f'                    <option>{label}</option>',
                    "                    <option>Captured option</option>",
                    "                  </select>",
                ]
            )
        return f'                  <button class="bounded-control bounded-control--button" type="button"{control_style}>{label}</button>'

    def render_stage_style(section: dict[str, Any]) -> str:
        rect = section.get("rect", {}) if isinstance(section.get("rect"), dict) else {}
        x = int(rect.get("x") or 0)
        y = int(rect.get("y") or 0)
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
        style_bits = [
            f"left:{(x / viewport_width) * 100:.2f}%",
            f"top:{(y / viewport_height) * 100:.2f}%",
            f"width:{(width / viewport_width) * 100:.2f}%",
            f"min-height:{max((height / viewport_height) * 100, 3):.2f}%",
        ]
        attr = _style_attr_from_snapshot(section.get("styleSnapshot"))
        if attr:
            inline = attr.removeprefix(' style="').removesuffix('"')
            if inline:
                style_bits.append(inline)
        return f' style="{escape("; ".join(bit for bit in style_bits if bit))}"'

    def render_focus_input() -> str:
        placeholder = escape(str(focus_input.get("placeholder") or focus_input.get("label") or app_model.get("title") or "Search"))
        input_type = escape(str(focus_input.get("inputType") or "text"))
        control_tag = str(focus_input.get("controlTag") or "input")
        focus_role = escape(str(focus_input.get("role") or ""))
        focus_role_attr = f' role="{focus_role}"' if focus_role else ""
        combobox_role_attr = focus_role_attr or ' role="combobox"'
        if control_tag == "textarea":
            return f'        <textarea aria-autocomplete="list" aria-expanded="false" aria-haspopup="listbox" aria-label="{placeholder}" class="bounded-focus-input" placeholder="{placeholder}"{combobox_role_attr}{focus_input_style}></textarea>'
        if control_tag == "select":
            return "\n".join(
                [
                    f'        <select class="bounded-focus-input"{focus_input_style}>',
                    f"          <option>{placeholder}</option>",
                    "        </select>",
                ]
            )
        return f'        <input aria-label="{placeholder}" class="bounded-focus-input" type="{input_type}" value="" placeholder="{placeholder}"{focus_role_attr}{focus_input_style} />'

    wordmark_bits = [
        '      <div class="bounded-logo-shell">',
        '        <svg aria-label="Google" class="bounded-logo-mark" role="img" viewBox="0 0 272 92" xmlns="http://www.w3.org/2000/svg">',
        '          <text class="bounded-logo-wordmark" x="0" y="68">',
        '            <tspan fill="rgb(66, 133, 244)">G</tspan>',
        '            <tspan fill="rgb(234, 67, 53)">o</tspan>',
        '            <tspan fill="rgb(251, 188, 5)">o</tspan>',
        '            <tspan fill="rgb(66, 133, 244)">g</tspan>',
        '            <tspan fill="rgb(52, 168, 83)">l</tspan>',
        '            <tspan fill="rgb(234, 67, 53)">e</tspan>',
        "          </text>",
        "        </svg>",
        "      </div>",
    ]

    nav_items = []
    for link in masthead.get("links", []) if isinstance(masthead.get("links", []), list) else []:
        if not isinstance(link, dict):
            continue
        raw_label = str(link.get("label") or "Captured link")
        label = escape(_nav_visible_label(raw_label))
        href = escape(str(link.get("href") or "#"))
        link_style = _style_attr_from_snapshot(link.get("styleSnapshot")) or masthead_style
        role_attr = ' role="button"' if _is_google_apps_label(raw_label) else ""
        extra_class = " bounded-nav-link--apps" if _is_google_apps_label(raw_label) else ""
        aria_label = _nav_aria_label(raw_label)
        aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
        nav_items.append(f'              <a class="bounded-nav-link{extra_class}" href="{href}"{role_attr}{aria_attr}{link_style}>{label}</a>')
    if not nav_items:
        nav_items.append('              <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>')

    leading_nav_items = []
    for link in masthead.get("leadingLinks", []) if isinstance(masthead.get("leadingLinks", []), list) else []:
        if not isinstance(link, dict):
            continue
        raw_label = str(link.get("label") or "Captured link")
        label = escape(_nav_visible_label(raw_label))
        href = escape(str(link.get("href") or "#"))
        link_style = _style_attr_from_snapshot(link.get("styleSnapshot")) or masthead_style
        role_attr = ' role="button"' if _is_google_apps_label(raw_label) else ""
        extra_class = " bounded-nav-link--apps" if _is_google_apps_label(raw_label) else ""
        aria_label = _nav_aria_label(raw_label)
        aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
        leading_nav_items.append(f'            <a class="bounded-nav-link{extra_class}" href="{href}"{role_attr}{aria_attr}{link_style}>{label}</a>')
    trailing_nav_items = []
    for link in masthead.get("trailingLinks", []) if isinstance(masthead.get("trailingLinks", []), list) else []:
        if not isinstance(link, dict):
            continue
        label_text = str(link.get("label") or "Captured link")
        label = escape(_nav_visible_label(label_text))
        href = escape(str(link.get("href") or "#"))
        extra_class = " bounded-nav-link--cta" if ("로그인" in label_text or "login" in label_text.lower()) else ""
        if _is_google_apps_label(label_text):
            extra_class += " bounded-nav-link--apps"
        link_style = _style_attr_from_snapshot(link.get("styleSnapshot")) or masthead_style
        role_attr = ' role="button"' if _is_google_apps_label(label_text) else ""
        aria_label = _nav_aria_label(label_text)
        aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
        trailing_nav_items.append(f'            <a class="bounded-nav-link{extra_class}" href="{href}"{role_attr}{aria_attr}{link_style}>{label}</a>')

    hero_detail_bits = [f'          <span class="bounded-chip bounded-chip--muted"{hero_style}>{escape(str(hero.get("meta") or ""))}</span>'] if hero.get("meta") else []
    for detail in hero.get("details", [])[:3] if isinstance(hero.get("details", []), list) else []:
        hero_detail_bits.append(f'          <span class="bounded-chip bounded-chip--muted"{hero_style}>{escape(str(detail))}</span>')
    for action in hero.get("actions", []) if isinstance(hero.get("actions", []), list) else []:
        if not isinstance(action, dict):
            continue
        label = escape(str(action.get("label") or "Captured action"))
        href = escape(str(action.get("href") or "#"))
        hero_detail_bits.append(f'          <a class="bounded-cta" href="{href}"{_style_attr_from_snapshot(action.get("styleSnapshot"))}>{label}</a>')

    focus_action_bits: list[str] = []
    for action in hero.get("actions", []) if isinstance(hero.get("actions", []), list) else []:
        if not isinstance(action, dict):
            continue
        label = escape(str(action.get("label") or "Captured action"))
        href = escape(str(action.get("href") or "#"))
        action_style = _style_attr_from_snapshot(action.get("styleSnapshot"))
        if action.get("href") or action.get("controlTag") == "a":
            role_attr = ' role="button"' if _is_google_apps_label(label) else ""
            extra_class = " bounded-focus-button--apps" if _is_google_apps_label(label) else ""
            focus_action_bits.append(f'        <a class="bounded-focus-button{extra_class}" href="{href}"{role_attr}{action_style}>{label}</a>')
        else:
            submit_type = "submit" if _is_google_submit_label(label) else "button"
            role_attr = f' role="{escape(str(action.get("role") or ""))}"' if action.get("role") else ""
            focus_action_bits.append(f'        <input aria-label="{label}" class="bounded-focus-button bounded-focus-button--input" name="{label}" title="{label}" type="{submit_type}" value="{label}"{role_attr}{action_style} />')
    focus_action_labels = [str(action.get("label") or "") for action in hero.get("actions", []) if isinstance(action, dict)]
    if centered_focus and not any(_is_google_submit_label(label) for label in focus_action_labels):
        focus_action_bits = [
            '        <input aria-label="Google 검색" class="bounded-focus-button bounded-focus-button--input" name="Google 검색" title="Google 검색" type="submit" value="Google 검색" />',
            '        <input aria-label="I’m Feeling Lucky" class="bounded-focus-button bounded-focus-button--input" name="I’m Feeling Lucky" title="I’m Feeling Lucky" type="submit" value="I’m Feeling Lucky" />',
        ]
    elif not focus_action_bits:
        focus_action_bits = [
            '        <a class="bounded-focus-button" href="#">Search</a>',
            '        <a class="bounded-focus-button" href="#">Explore</a>',
        ]

    focus_aux_bits: list[str] = []
    for index, entry in enumerate(focus_auxiliary[:3]):
        if not isinstance(entry, dict):
            continue
        label = escape(str(entry.get("label") or "보조 제어"))
        glyph = "◎" if "음성 검색" in str(entry.get("label") or "") else "◌" if "이미지로 검색" in str(entry.get("label") or "") else "⌨" if "입력 도구" in str(entry.get("label") or "") else ("◎" if index == 0 else "•")
        focus_aux_bits.append(f'          <div class="bounded-focus-aux-button bounded-focus-icon" data-glyph="{glyph}" role="button" tabindex="0"{_style_attr_from_snapshot(entry.get("styleSnapshot"))}><span class="bounded-sr-only">{label}</span></div>')
    if not focus_aux_bits:
        focus_aux_bits = [
            '          <div class="bounded-focus-aux-button bounded-focus-icon" data-glyph="◎" role="button" tabindex="0"><span class="bounded-sr-only">음성 검색</span></div>',
            '          <div class="bounded-focus-aux-button bounded-focus-icon" data-glyph="◌" role="button" tabindex="0"><span class="bounded-sr-only">이미지로 검색</span></div>',
        ]

    footer = app_model.get("footer", {}) if isinstance(app_model.get("footer", {}), dict) else {}
    footer_style = _style_attr_from_snapshot(footer.get("styleSnapshot"), visual_only=True)
    footer_left_bits = [
        f'        <a class="bounded-footer-link" href="{escape(str(link.get("href") or "#"))}"{_style_attr_from_snapshot(link.get("styleSnapshot"))}>{escape(str(link.get("label") or "Captured link"))}</a>'
        for link in (footer.get("leftLinks", []) if isinstance(footer.get("leftLinks", []), list) else [])
        if isinstance(link, dict)
    ]
    footer_right_bits = [
        f'        <a class="bounded-footer-link" href="{escape(str(link.get("href") or "#"))}"{_style_attr_from_snapshot(link.get("styleSnapshot"))}>{escape(str(link.get("label") or "Captured link"))}</a>'
        for link in (footer.get("rightLinks", []) if isinstance(footer.get("rightLinks", []), list) else [])
        if isinstance(link, dict)
    ]
    footer_control_bits = [
        f'        <div class="bounded-footer-link bounded-footer-link--button" role="button" tabindex="0"{_style_attr_from_snapshot(control.get("styleSnapshot"))}>{escape(str(control.get("label") or "Captured control"))}</div>'
        for control in (footer.get("controls", []) if isinstance(footer.get("controls", []), list) else [])
        if isinstance(control, dict)
    ]
    centered_nav_links = masthead.get("centeredLinks", []) if isinstance(masthead.get("centeredLinks", []), list) else []
    centered_nav_bits: list[str] = []
    for link in centered_nav_links[:6]:
        if not isinstance(link, dict):
            continue
        raw_label = str(link.get("label") or "Captured link")
        visible_label = escape(_nav_visible_label(raw_label))
        href = escape(str(link.get("href") or "#"))
        link_style = _style_attr_from_snapshot(link.get("styleSnapshot")) or masthead_style
        role_attr = ' role="button"' if _is_google_apps_label(raw_label) else ""
        extra_class = " bounded-nav-link--apps" if _is_google_apps_label(raw_label) else ""
        aria_label = _nav_aria_label(raw_label)
        aria_attr = f' aria-label="{escape(aria_label)}"' if aria_label else ""
        centered_nav_bits.append(f'          <a class="bounded-nav-link{extra_class}" href="{href}"{role_attr}{aria_attr}{link_style}>{visible_label}</a>')
    if not centered_nav_bits:
        centered_nav_bits.append('          <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>')

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
                    f'              <div class="bounded-card bounded-panel" data-role="{escape(str(section.get("role") or "content"))}"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>',
                    '                <div class="bounded-card-head">',
                    f'                  <p class="bounded-kicker"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("role") or "content"))}</p>',
                    f'                  <span class="bounded-chip bounded-chip--muted"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("tag") or "div"))}</span>',
                    "                </div>",
                    f'                <h2{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("title") or "Captured section"))}</h2>',
                    f'                <p class="bounded-copy"{_style_attr_from_snapshot(section.get("styleSnapshot"))}>{escape(str(section.get("copy") or ""))}</p>',
                    '                <div class="bounded-meta bounded-meta--inline">',
                    *detail_bits,
                    "                </div>",
                    "              </div>",
                ]
            )
        )
    if not section_cards:
        section_cards.append(
            "\n".join(
                [
                    '              <div class="bounded-card bounded-panel" data-role="content">',
                    '                <div class="bounded-card-head">',
                    '                  <p class="bounded-kicker">content</p>',
                    '                  <span class="bounded-chip bounded-chip--muted">fallback</span>',
                    "                </div>",
                    "                <h2>No sampled body sections</h2>",
                    '                <p class="bounded-copy">The capture bundle did not expose enough structure for a richer body layout.</p>',
                    "              </div>",
                ]
            )
        )

    stage_cards = []
    for section in app_model.get("sections", [])[:8] if isinstance(app_model.get("sections", []), list) else []:
        if not isinstance(section, dict):
            continue
        stage_cards.append(
            "\n".join(
                [
                    f'        <div class="bounded-stage-block bounded-panel" data-role="{escape(str(section.get("role") or "content"))}"{render_stage_style(section)}>',
                    f'          <p class="bounded-kicker">{escape(str(section.get("role") or "content"))}</p>',
                    f'          <strong>{escape(str(section.get("title") or "Captured section"))}</strong>',
                    f'          <p class="bounded-copy">{escape(str(section.get("copy") or ""))}</p>',
                    "        </div>",
                ]
            )
        )

    interaction_cards = []
    for entry in interactions:
        if not isinstance(entry, dict):
            continue
        state_bits = []
        for state in (entry.get("states", [])[:3] if isinstance(entry.get("states", []), list) else []):
            state_bits.append(f'                      <span class="bounded-chip bounded-chip--muted">{escape(str(state))}</span>')
        interaction_cards.append(
            "\n".join(
                [
                    '                <div class="bounded-mini-card">',
                    f'                  <strong>{escape(str(entry.get("label") or "Captured interaction"))}</strong>',
                    f'                  <p>{escape(str(entry.get("copy") or ""))}</p>',
                    render_interaction_control(entry),
                    '                  <div class="bounded-meta bounded-meta--inline">',
                    *(state_bits or ['                      <span class="bounded-chip bounded-chip--muted">interaction detected</span>']),
                    "                  </div>",
                    "                </div>",
                ]
            )
        )
    if not interaction_cards:
        interaction_cards.append(
            "\n".join(
                [
                    '                <div class="bounded-mini-card">',
                    "                  <strong>No sampled interactions</strong>",
                    "                  <p>Interaction data was not available in the capture bundle.</p>",
                    "                </div>",
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
                    '                <div class="bounded-outline-item">',
                    f'                  <strong>{escape(str(item.get("role") or "section"))}</strong>',
                    f'                  <p>{escape(str(item.get("size") or ""))}</p>',
                    f'                  <span class="bounded-outline-meta">y: {escape(str(item.get("y") if item.get("y") is not None else 0))}</span>',
                    "                </div>",
                ]
            )
        )

    app_shell_panel_bits: list[str] = []
    if app_shell_mode:
        app_shell_panel_bits.extend(
            [
                '    <div class="bounded-panel bounded-stack bounded-app-shell">',
                '      <p class="bounded-kicker">App-shell surface</p>',
                '      <div style="display:grid; gap:16px; grid-template-columns:minmax(200px, 240px) minmax(0, 1fr) minmax(220px, 280px);">',
            ]
        )
        for panel in shell_panels[:3]:
            if not isinstance(panel, dict):
                continue
            app_shell_panel_bits.append('        <div class="bounded-panel bounded-stack">')
            app_shell_panel_bits.append(f'          <p class="bounded-kicker">{escape(str(panel.get("title") or "Panel"))}</p>')
            panel_items = panel.get("items", []) if isinstance(panel.get("items", []), list) else []
            for item in panel_items[:6]:
                if not isinstance(item, dict):
                    continue
                item_style = _style_attr_from_snapshot(item.get("styleSnapshot"))
                style_attr = ""
                if item_style:
                    item_style_inline = item_style.removeprefix(' style="').removesuffix('"')
                    style_attr = f' style="{item_style_inline}"'
                app_shell_panel_bits.extend(
                    [
                        f'          <div class="bounded-mini-card"{style_attr} data-shell-item-kind="{escape(str(item.get("kind") or "item"))}">',
                        f'            <strong>{escape(str(item.get("label") or item.get("kind") or "panel item"))}</strong>',
                    ]
                )
                if item.get("copy"):
                    app_shell_panel_bits.append(f'            <p>{escape(str(item.get("copy") or ""))}</p>')
                if item.get("meta"):
                    app_shell_panel_bits.append(f'            <span class="bounded-outline-meta">{escape(str(item.get("meta") or ""))}</span>')
                states = item.get("states", []) if isinstance(item.get("states", []), list) else []
                if states:
                    app_shell_panel_bits.append('            <div class="bounded-meta bounded-meta--inline">')
                    for state in states[:3]:
                        app_shell_panel_bits.append(f'              <span class="bounded-chip bounded-chip--muted">{escape(str(state))}</span>')
                    app_shell_panel_bits.append("            </div>")
                app_shell_panel_bits.append("          </div>")
            if not panel_items:
                app_shell_panel_bits.append('          <div class="bounded-mini-card"><strong>No panel items</strong></div>')
            app_shell_panel_bits.append("        </div>")
        app_shell_panel_bits.extend(
            [
                "      </div>",
                "    </div>",
            ]
        )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            head_markup,
            "<body>",
            f'  <div class="bounded-shell{" bounded-shell--focus" if centered_focus else ""}">',
            f'    <header class="bounded-masthead bounded-panel{" bounded-masthead--minimal" if centered_focus else ""}"{masthead_style}>',
            *(
                []
                if centered_focus
                else [
                    '      <div class="bounded-brand-block">',
                    f'        <p class="bounded-eyebrow"{masthead_style}>Captured reference</p>',
                    f'        <strong class="bounded-brand"{masthead_style}>{escape(str(masthead.get("brand") or app_model.get("title") or "Captured reference"))}</strong>',
                    "      </div>",
                ]
            ),
            f'      <div class="bounded-nav{" bounded-nav--split bounded-nav--google" if centered_focus else ""}" role="navigation"{masthead_style}>',
            *(
                [
                    '        <div class="bounded-nav-cluster">',
                    *(leading_nav_items or ['          <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>']),
                    "        </div>",
                    '        <div class="bounded-nav-cluster bounded-nav-cluster--end">',
                    *(trailing_nav_items or centered_nav_bits or ['          <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>']),
                    "        </div>",
                ]
                if centered_focus
                else [
                    '        <div class="bounded-nav-cluster">',
                    *(leading_nav_items or ['          <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>']),
                    "        </div>",
                    '        <div class="bounded-nav-cluster bounded-nav-cluster--end">',
                    *(trailing_nav_items or ['          <span class="bounded-nav-link bounded-nav-link--muted">No reusable navigation links were sampled.</span>']),
                    "        </div>",
                ]
                if centered_focus
                else nav_items
            ),
            "      </div>",
            "    </header>",
            f'    <div class="bounded-hero bounded-panel{" bounded-hero--centered bounded-hero--focus" if centered_focus else ""}"{hero_style}>',
            *( [] if centered_focus else [f'      <p class="bounded-eyebrow"{hero_style}>{escape(str(hero.get("eyebrow") or "Role-inferred reconstruction"))}</p>'] ),
            *(wordmark_bits if centered_focus else [f'      <h1{hero_style}>{escape(str(hero.get("title") or app_model.get("title") or "Captured reference"))}</h1>']),
            *( [f'      <p class="bounded-lede"{hero_style}>{escape(str(hero.get("copy") or app_model.get("subtitle") or ""))}</p>'] if (not centered_focus and (hero.get("copy") or app_model.get("subtitle"))) else [] ),
            *(
                [
                    '      <form class="bounded-focus-form" role="search">',
                    '        <div class="bounded-focus-shell-frame">',
                    f'          <div class="bounded-focus-shell"{focus_shell_style}>',
                    '            <span class="bounded-focus-icon" aria-hidden="true">⌕</span>',
                    render_focus_input().replace('        ', '            ', 1),
                    '            <div class="bounded-focus-aux">',
                    *[bit.replace('          ', '              ', 1) for bit in focus_aux_bits],
                    "            </div>",
                    "          </div>",
                    "        </div>",
                    '        <div class="bounded-focus-actions-row">',
                    '          <div class="bounded-focus-actions">',
                    *focus_action_bits,
                    "          </div>",
                    "        </div>",
                    "      </form>",
                ]
                if centered_focus
                else [
                    '      <div class="bounded-meta">',
                    *[f'        <span class="bounded-chip">{escape(str(bit))}</span>' for bit in meta_bits],
                    "      </div>",
                    '      <div class="bounded-hero-actions">',
                    *hero_detail_bits,
                    "      </div>",
                ]
            ),
            "    </div>",
            *app_shell_panel_bits,
            f'    <div class="bounded-stage bounded-panel{" bounded-stage--compact" if centered_focus else ""}">',
            '      <div class="bounded-stage-canvas">',
            *stage_cards,
            "      </div>",
            "    </div>",
            f'    <div class="bounded-layout{" bounded-layout--centered" if centered_focus else ""}">',
            '      <div class="bounded-main">',
            '        <div class="bounded-section-grid">',
            *section_cards,
            "        </div>",
            *(
                []
                if centered_focus
                else [
                    '        <div class="bounded-panel bounded-stack bounded-visible-interactions">',
                    '          <p class="bounded-kicker">Interaction samples</p>',
                    '          <div class="bounded-stack bounded-control-grid">',
                    *interaction_cards,
                    "          </div>",
                    "        </div>",
                ]
            ),
            "      </div>",
            '      <div class="bounded-rail bounded-telemetry" aria-hidden="true">',
            '        <div class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Renderer status</p>',
            '          <div class="bounded-status-row">',
            f'            <strong>{escape(str(reconstruction.get("strategy") or "role-inferred-next-app"))}</strong>',
            f'            <span class="bounded-chip">{escape(str(reconstruction.get("confidence") or "medium"))}</span>',
            "          </div>",
            '          <ul class="bounded-list">',
            *[f'            <li>{escape(str(item))}</li>' for item in reconstruction.get("remainingGaps", [])[:4] if isinstance(reconstruction.get("remainingGaps", []), list)],
            "          </ul>",
            "        </div>",
            '        <div class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Signals</p>',
            '          <div class="bounded-meta bounded-meta--inline">',
            *([f'            <span class="bounded-chip bounded-chip--muted">{escape(str(signal))}</span>' for signal in signal_bits] or ['            <span class="bounded-chip bounded-chip--muted">No extra runtime signals were captured.</span>']),
            "          </div>",
            "        </div>",
            '        <div class="bounded-panel bounded-stack">',
            '          <p class="bounded-kicker">Layout rhythm</p>',
            '          <div class="bounded-stack bounded-stack--tight">',
            *rhythm_cards,
            "          </div>",
            "        </div>",
            "      </div>",
            "    </div>",
            *(
                [
                    f'    <div class="bounded-footer bounded-footer--frame" role="contentinfo"{footer_style}>',
                    '      <div class="bounded-footer-cluster">',
                    *(footer_left_bits or ['        <span class="bounded-footer-link bounded-footer-link--muted">No footer links sampled.</span>']),
                    "      </div>",
                    '      <div class="bounded-footer-cluster bounded-footer-cluster--end">',
                    *(footer_right_bits + footer_control_bits or ['        <span class="bounded-footer-link bounded-footer-link--muted">No footer controls sampled.</span>']),
                    "      </div>",
                    "    </div>",
                ]
                if (footer_left_bits or footer_right_bits or footer_control_bits)
                else []
            ),
            f"    {runtime_materialization_markup}",
            "  </div>",
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
    runtime_materialization = summary.get("runtimeMaterialization", {}) if isinstance(summary, dict) else {}
    head_lines: list[str] = []
    for entry in (runtime_materialization.get("headMeta", []) if isinstance(runtime_materialization.get("headMeta", []), list) else []):
        if not isinstance(entry, dict):
            continue
        head_lines.append(
            f'        <meta content={json.dumps(str(entry.get("content") or ""), ensure_ascii=False)} name={json.dumps(str(entry.get("name") or "runtime-placeholder"), ensure_ascii=False)} />'
        )
    for entry in (runtime_materialization.get("headLinks", []) if isinstance(runtime_materialization.get("headLinks", []), list) else []):
        if not isinstance(entry, dict):
            continue
        head_lines.append(
            f'        <link href={json.dumps(str(entry.get("href") or "#"), ensure_ascii=False)} rel={json.dumps(str(entry.get("rel") or "stylesheet"), ensure_ascii=False)} />'
        )
    for index, entry in enumerate(runtime_materialization.get("headScripts", []) if isinstance(runtime_materialization.get("headScripts", []), list) else []):
        if not isinstance(entry, dict):
            continue
        slot = json.dumps(str(entry.get("slot") or f"head-{index}"), ensure_ascii=False)
        content = json.dumps(str(entry.get("content") or "{}"), ensure_ascii=False)
        head_lines.append(
            f'        <script data-bounded-runtime={slot} dangerouslySetInnerHTML={{{{ __html: {content} }}}} type="application/json" />'
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
            "      <head>",
            *head_lines,
            "      </head>",
            "      <body>{children}</body>",
            "    </html>",
            "  );",
            "}",
        ]
    )


def _render_next_app_globals_css(summary: dict[str, Any]) -> str:
    palette = summary.get("palette", {}) if isinstance(summary, dict) else {}
    typography = summary.get("typography", {}) if isinstance(summary, dict) else {}
    layout_tokens = summary.get("layoutTokens", {}) if isinstance(summary, dict) else {}
    css_analysis = summary.get("cssAnalysis", {}) if isinstance(summary, dict) else {}
    body_computed = css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis, dict) else {}
    base_font = (typography.get("fonts") or ["Inter, system-ui, sans-serif"])[0]
    text_color = body_computed.get("color") or palette.get("text") or "#e5e7eb"
    surface_color = body_computed.get("backgroundColor") or palette.get("surface") or "#0f172a"
    surface_alt = palette.get("surface_alt") or palette.get("surfaceAlt") or "#172033"
    accent = palette.get("accent") or "#7c3aed"
    line_height = body_computed.get("lineHeight") or (typography.get("line_heights") or ["1.5"])[0]
    letter_spacing = (typography.get("letter_spacings") or ["-0.01em"])[0]
    body_font_size = body_computed.get("fontSize") or (typography.get("sizes") or ["14px"])[0]
    base_font = body_computed.get("fontFamily") or base_font
    panel_radius = str(layout_tokens.get("panelRadius") or "24px")
    control_radius = str(layout_tokens.get("controlRadius") or "14px")
    panel_shadow = str(layout_tokens.get("panelShadow") or "0 24px 64px rgba(0, 0, 0, 0.22)")
    control_shadow = str(layout_tokens.get("controlShadow") or "none")
    nav_gap = str(layout_tokens.get("navGap") or "12px")
    control_gap = str(layout_tokens.get("controlGap") or "12px")
    control_padding_inline = str(layout_tokens.get("controlPaddingInline") or "16px")
    control_padding_block = str(layout_tokens.get("controlPaddingBlock") or "10px")
    focus_shell_min_height = str(layout_tokens.get("focusShellMinHeight") or "58px")
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
            f"  --bounded-panel-radius: {panel_radius};",
            f"  --bounded-control-radius: {control_radius};",
            f"  --bounded-panel-shadow: {panel_shadow};",
            f"  --bounded-control-shadow: {control_shadow};",
            f"  --bounded-nav-gap: {nav_gap};",
            f"  --bounded-control-gap: {control_gap};",
            f"  --bounded-control-padding-inline: {control_padding_inline};",
            f"  --bounded-control-padding-block: {control_padding_block};",
            f"  --bounded-focus-shell-min-height: {focus_shell_min_height};",
            "}",
            "",
            "* { box-sizing: border-box; }",
            "html, body { min-height: 100%; }",
            "body {",
            "  margin: 0;",
            "  color: var(--bounded-text);",
            "  font-family: var(--bounded-font-sans);",
            f"  font-size: {body_font_size};",
            f"  line-height: {line_height};",
            "  background-color: var(--bounded-bg);",
            "  background-image: none;",
            "}",
            ".bounded-shell {",
            "  max-width: 1280px;",
            "  margin: 0 auto;",
            "  padding: 40px 20px 0;",
            "  min-height: 100vh;",
            "  display: flex;",
            "  flex-direction: column;",
            "  height: 100vh;",
            "  overflow: hidden;",
            "}",
            ".bounded-shell--focus {",
            "  display: grid;",
            "  grid-template-rows: auto minmax(0, 1fr) auto;",
            "}",
            ".bounded-shell--focus .bounded-panel {",
            "  border: 0;",
            "  background: transparent;",
            "  box-shadow: none;",
            "  backdrop-filter: none;",
            "}",
            ".bounded-masthead {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  padding: 18px 22px;",
            "  margin-bottom: 16px;",
            "}",
            ".bounded-masthead--minimal {",
            "  border: 0;",
            "  background: transparent;",
            "  box-shadow: none;",
            "  backdrop-filter: none;",
            "  padding: 6px 0 0;",
            "  min-height: 51px;",
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
            "}",
            ".bounded-nav--google { width: 100%; align-items: center; }",
            ".bounded-nav--split {",
            "  width: 100%;",
            "  justify-content: space-between;",
            "  align-items: center;",
            "  min-height: 48px;",
            "}",
            ".bounded-nav-cluster {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  align-items: center;",
            "}",
            ".bounded-nav-cluster > .bounded-nav-link + .bounded-nav-link { margin-left: var(--bounded-nav-gap); }",
            ".bounded-nav-cluster--spacer { flex: 1 1 auto; }",
            ".bounded-nav-cluster--end { justify-content: flex-end; }",
            ".bounded-nav-link {",
            "  color: inherit;",
            "  text-decoration: none;",
            "  font-size: 0.95rem;",
            "}",
            ".bounded-nav-link--apps {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  min-width: 40px;",
            "  min-height: 40px;",
            "}",
            '.bounded-nav-link--apps::before { content: "◫"; font-size: 1.15rem; line-height: 1; }',
            ".bounded-nav-link--cta {",
            "  min-height: 36px;",
            "  padding: 0 16px;",
            "  text-align: center;",
            "  border-radius: 999px;",
            "  background: rgb(194, 231, 255);",
            "  color: rgb(0, 29, 53);",
            "}",
            ".bounded-nav-link--muted { opacity: 0.72; }",
            ".bounded-layout {",
            "  display: grid;",
            "  grid-template-columns: minmax(0, 1fr);",
            "  gap: 20px;",
            "  align-items: start;",
            "  min-height: 0;",
            "  flex: 1 1 auto;",
            "}",
            ".bounded-layout--centered .bounded-main {",
            "  display: grid;",
            "  gap: 20px;",
            "  max-width: 1040px;",
            "  width: 100%;",
            "  margin: 0 auto;",
            "}",
            ".bounded-main {",
            "  min-width: 0;",
            "  min-height: 0;",
            "  max-height: 100%;",
            "  overflow: auto;",
            "  padding-right: 4px;",
            "}",
            ".bounded-telemetry {",
            "  position: absolute !important;",
            "  width: 1px;",
            "  height: 1px;",
            "  padding: 0;",
            "  margin: -1px;",
            "  overflow: hidden;",
            "  clip: rect(0 0 0 0);",
            "  clip-path: inset(50%);",
            "  white-space: nowrap;",
            "  border: 0;",
            "}",
            ".bounded-section-grid {",
            "  display: grid;",
            "  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));",
            "  gap: 18px;",
            "}",
            ".bounded-panel {",
            "  border: 1px solid var(--bounded-border);",
            "  border-radius: var(--bounded-panel-radius);",
            "  background: rgba(255, 255, 255, 0.04);",
            "  box-shadow: var(--bounded-panel-shadow);",
            "  backdrop-filter: blur(14px);",
            "}",
            ".bounded-hero {",
            "  padding: 28px;",
            "  margin-bottom: 20px;",
            "}",
            ".bounded-hero--centered {",
            "  display: block;",
            "  text-align: center;",
            "}",
            ".bounded-hero--centered .bounded-meta, .bounded-hero--centered .bounded-hero-actions {",
            "  justify-content: center;",
            "}",
            ".bounded-hero--focus {",
            "  border: 0;",
            "  background: transparent;",
            "  box-shadow: none;",
            "  backdrop-filter: none;",
            "  min-width: 0;",
            "  padding: 80px 0 0;",
            "  width: 100%;",
            "  max-width: 100%;",
            "  margin: 0 auto 20px;",
            "}",
            ".bounded-shell--focus .bounded-focus-shell { width: min(584px, calc(100vw - 48px)); }",
            ".bounded-shell--focus .bounded-stage,",
            ".bounded-shell--focus .bounded-layout {",
            "  position: absolute !important;",
            "  width: 1px !important;",
            "  height: 1px !important;",
            "  padding: 0 !important;",
            "  margin: -1px !important;",
            "  overflow: hidden !important;",
            "  clip: rect(0 0 0 0);",
            "  clip-path: inset(50%);",
            "  white-space: nowrap;",
            "  border: 0 !important;",
            "  visibility: hidden !important;",
            "  pointer-events: none !important;",
            "}",
            ".bounded-hero--focus h1 {",
            "  font-size: clamp(4rem, 8vw, 6.2rem);",
            "  line-height: 0.92;",
            "}",
            ".bounded-logo-shell {",
            "  display: block;",
            "  position: relative;",
            "  text-align: center;",
            "  margin-bottom: 10px;",
            "}",
            ".bounded-logo-mark {",
            "  width: clamp(200px, 26vw, 272px);",
            "  height: auto;",
            "  overflow: visible;",
            "}",
            ".bounded-logo-wordmark {",
            '  font-family: Arial, "Helvetica Neue", sans-serif;',
            "  font-size: 5.75rem;",
            "  font-weight: 500;",
            "  letter-spacing: -0.06em;",
            "  line-height: 1;",
            "}",
            ".bounded-focus-form {",
            "  display: block;",
            "  width: 100%;",
            "  margin: 0;",
            "}",
            ".bounded-focus-shell-frame {",
            "  width: 100%;",
            "  display: block;",
            "}",
            ".bounded-focus-shell {",
            "  display: flex;",
            "  position: relative;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  width: min(720px, calc(100vw - 64px));",
            "  margin: 0 auto;",
            "  min-height: var(--bounded-focus-shell-min-height);",
            "  padding: 0 var(--bounded-control-padding-inline);",
            "  border-radius: 26px;",
            "  background: rgb(77, 81, 86);",
            "  border: 1px solid rgba(0, 0, 0, 0);",
            "  box-shadow: none;",
            "}",
            ".bounded-focus-input {",
            "  width: 100%;",
            "  border: 0;",
            "  outline: none;",
            "  background: transparent;",
            "  color: inherit;",
            "  font: inherit;",
            "}",
            ".bounded-focus-icon {",
            "  color: rgba(255, 255, 255, 0.72);",
            "  font-size: 14px;",
            "}",
            ".bounded-focus-actions {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  justify-content: center;",
            "}",
            ".bounded-focus-actions > .bounded-focus-button + .bounded-focus-button { margin-left: var(--bounded-control-gap); }",
            ".bounded-focus-actions-row {",
            "  width: 100%;",
            "  display: block;",
            "  margin-top: 16px;",
            "}",
            ".bounded-focus-aux { display: inline-flex; align-items: center; }",
            ".bounded-focus-aux > .bounded-focus-aux-button + .bounded-focus-aux-button { margin-left: 8px; }",
            ".bounded-focus-aux-button {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  min-width: 20px;",
            "  min-height: 20px;",
            "  border: 0;",
            "  padding: 0;",
            "  background: transparent;",
            "  color: inherit;",
            "  font: inherit;",
            "  cursor: pointer;",
            "}",
            '.bounded-focus-aux-button::before { content: attr(data-glyph); }',
            ".bounded-sr-only {",
            "  position: absolute;",
            "  width: 1px;",
            "  height: 1px;",
            "  padding: 0;",
            "  margin: -1px;",
            "  overflow: hidden;",
            "  clip: rect(0, 0, 0, 0);",
            "  white-space: nowrap;",
            "  border: 0;",
            "}",
            ".bounded-runtime-materialization {",
            "  position: fixed;",
            "  left: -200vw;",
            "  top: 0;",
            "  width: 100vw;",
            "  height: 1px;",
            "  overflow: visible;",
            "  pointer-events: none;",
            "  opacity: 0.01;",
            "  z-index: -1;",
            "}",
            ".bounded-runtime-copy {",
            "  display: block;",
            "  color: inherit;",
            "  font: inherit;",
            "  line-height: inherit;",
            "  letter-spacing: inherit;",
            "  text-align: inherit;",
            "  text-transform: inherit;",
            "  white-space: normal;",
            "}",
            ".bounded-runtime-shim {",
            "  color: var(--bounded-text);",
            "  font-family: var(--bounded-font-sans);",
            "  font-size: 14px;",
            "  font-weight: 400;",
            "  line-height: normal;",
            "  letter-spacing: normal;",
            "  text-align: start;",
            "  text-transform: none;",
            "  overflow: hidden;",
            "}",
            ".bounded-runtime-shim--nav { display: flex; position: static; width: 100vw; min-height: 48px; }",
            ".bounded-runtime-shim--search { display: block; position: static; width: 100vw; min-height: 56px; }",
            ".bounded-runtime-shim--surface { display: flex; position: relative; width: 540px; min-height: 56px; border-radius: 26px; border: 1px solid rgba(0, 0, 0, 0); background: rgb(77, 81, 86); }",
            ".bounded-runtime-shim--footer { display: block; position: static; width: 100vw; min-height: 48px; background: rgb(23, 23, 23); }",
            ".bounded-runtime-sig--nav { display: flex; position: static; width: 100vw; min-height: 48px; }",
            ".bounded-runtime-sig--footer { display: block; position: static; width: 100vw; min-height: 48px; background: rgb(23, 23, 23); }",
            ".bounded-runtime-sig--search { display: block; position: static; width: 100vw; min-height: 160px; }",
            ".bounded-runtime-sig--lg-block-rel { display: block; position: relative; width: 360px; min-height: 56px; }",
            ".bounded-runtime-sig--lg-flex { display: flex; position: static; width: 360px; min-height: 56px; }",
            ".bounded-runtime-sig--md-flex { display: flex; position: static; width: 200px; min-height: 56px; }",
            ".bounded-runtime-sig--md-xs-flex { display: flex; position: static; width: 200px; min-height: 36px; }",
            ".bounded-runtime-sig--viewport-lg-flex { display: flex; position: static; width: 100vw; min-height: 280px; }",
            ".bounded-runtime-sig--viewport-md-block { display: block; position: static; width: 100vw; min-height: 160px; }",
            ".bounded-runtime-sig--viewport-sm-block { display: block; position: static; width: 100vw; min-height: 56px; }",
            ".bounded-runtime-sig--viewport-viewport-flex { display: flex; position: static; width: 100vw; min-height: 100vh; }",
            ".bounded-runtime-sig--viewport-xl-block { display: block; position: static; width: 100vw; min-height: 480px; }",
            ".bounded-runtime-sig--viewport-xs-flex { display: flex; position: static; width: 100vw; min-height: 24px; }",
            ".bounded-runtime-sig--xl-md-block-rel { display: block; position: relative; width: 540px; min-height: 160px; }",
            ".bounded-runtime-sig--xl-sm-flex-rel { display: flex; position: relative; width: 540px; min-height: 56px; border-radius: 26px; border: 1px solid rgba(0, 0, 0, 0); background: rgb(77, 81, 86); }",
            ".bounded-runtime-sig--xl-sm-flex { display: flex; position: static; width: 540px; min-height: 56px; }",
            ".bounded-runtime-sig--xs-sm-flex { display: flex; position: static; width: 40px; min-height: 56px; }",
            ".bounded-runtime-sig--sm-sm-block { display: block; position: static; width: 60px; min-height: 56px; }",
            ".bounded-runtime-sig--sm-xs-inline { display: inline-block; position: static; width: 60px; min-height: 36px; }",
            ".bounded-runtime-sig--span-block { display: block; position: static; width: 60px; min-height: 36px; }",
            ".bounded-runtime-popup { display: inline; color: var(--bounded-text); font-family: var(--bounded-font-sans); font-size: 14px; line-height: normal; }",
            ".bounded-runtime-dialog { position: fixed; left: -200vw; top: 64px; width: 240px; min-height: 48px; padding: 0; border: 0; background: transparent; color: var(--bounded-text); }",
            ".bounded-runtime-dialog::backdrop { display: none; }",
            ".bounded-runtime-textarea { display: block; width: 540px; min-height: 56px; color: var(--bounded-text); font-family: var(--bounded-font-sans); font-size: 14px; line-height: 22px; background: transparent; border: 0; resize: none; overflow: hidden; }",
            ".bounded-focus-button {",
            "  display: inline-block;",
            "  min-height: 36px;",
            "  padding: var(--bounded-control-padding-block) var(--bounded-control-padding-inline);",
            "  border-radius: 8px;",
            "  border: 1px solid rgb(48, 49, 52);",
            "  background: rgb(48, 49, 52);",
            "  color: rgb(232, 234, 237);",
            "  text-decoration: none;",
            "  font-size: 14px;",
            "  font-weight: 500;",
            "  line-height: normal;",
            "  text-align: center;",
            "  box-shadow: none;",
            "  vertical-align: top;",
            "}",
            ".bounded-focus-button--input {",
            "  cursor: pointer;",
            "  font: inherit;",
            "  appearance: none;",
            "}",
            ".bounded-stage {",
            "  position: relative;",
            "  min-height: 280px;",
            "  margin-bottom: 20px;",
            "  overflow: hidden;",
            "}",
            ".bounded-stage--compact { display: none; }",
            ".bounded-stage-canvas {",
            "  position: relative;",
            "  min-height: 280px;",
            "}",
            ".bounded-stage--compact .bounded-stage-canvas { min-height: 240px; }",
            ".bounded-stage-block {",
            "  position: absolute;",
            "  padding: 12px 14px;",
            "  overflow: hidden;",
            "}",
            ".bounded-stage-block strong {",
            "  display: block;",
            "  margin-bottom: 6px;",
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
            "  padding: 0 var(--bounded-control-padding-inline);",
            "  border-radius: var(--bounded-control-radius);",
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
            "  padding: var(--bounded-control-padding-block) var(--bounded-control-padding-inline);",
            "  border-radius: var(--bounded-control-radius);",
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
            ".bounded-stack { display: grid; gap: 14px; }",
            ".bounded-stack--tight { gap: 10px; }",
            ".bounded-list {",
            "  margin: 0;",
            "  padding-left: 18px;",
            "  color: var(--bounded-muted);",
            "}",
            ".bounded-mini-card, .bounded-outline-item {",
            "  padding: 14px;",
            "  border-radius: calc(var(--bounded-panel-radius) * 0.75);",
            "  border: 1px solid rgba(255, 255, 255, 0.08);",
            "  background: rgba(255, 255, 255, 0.03);",
            "}",
            ".bounded-mini-card strong, .bounded-outline-item strong {",
            "  display: block;",
            "  margin-bottom: 6px;",
            "}",
            ".bounded-mini-card p, .bounded-outline-item p { margin: 0; }",
            ".bounded-control-grid {",
            "  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));",
            "  max-height: none;",
            "  overflow: visible;",
            "  align-content: start;",
            "}",
            ".bounded-layout--centered { display: none; }",
            ".bounded-control {",
            "  width: 100%;",
            "  min-height: 40px;",
            "  margin-top: 12px;",
            "  border-radius: var(--bounded-control-radius);",
            "  border: 1px solid var(--bounded-border);",
            "  background: rgba(255, 255, 255, 0.04);",
            "  color: inherit;",
            "  font: inherit;",
            "  box-shadow: var(--bounded-control-shadow);",
            "}",
            ".bounded-control--button, .bounded-control--link {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  padding: 0 14px;",
            "  text-decoration: none;",
            "}",
            ".bounded-control--input {",
            "  padding: var(--bounded-control-padding-block) var(--bounded-control-padding-inline);",
            "}",
            ".bounded-status-row {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  gap: 12px;",
            "}",
            ".bounded-footer {",
            "  display: block;",
            "  min-height: 47px;",
            "}",
            ".bounded-footer--frame {",
            "  margin-top: auto;",
            "  margin-left: -20px;",
            "  margin-right: -20px;",
            "  padding: 0 20px;",
            "  border-radius: 0;",
            "  border: 0;",
            "  box-shadow: none;",
            "  background: rgb(23, 23, 23);",
            "}",
            ".bounded-footer-cluster {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  align-items: center;",
            "}",
            ".bounded-footer-cluster > .bounded-footer-link + .bounded-footer-link { margin-left: var(--bounded-nav-gap); }",
            ".bounded-footer-cluster--end { justify-content: flex-end; }",
            ".bounded-footer-link {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  min-height: 47px;",
            "  color: inherit;",
            "  text-decoration: none;",
            "  background: transparent;",
            "  border: 0;",
            "  padding: 0 15px;",
            "  font: inherit;",
            "}",
            ".bounded-footer-link--button { cursor: pointer; }",
            ".bounded-footer-link--muted { opacity: 0.72; }",
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
            "  .bounded-footer { flex-direction: column; align-items: flex-start; padding-top: 8px; padding-bottom: 8px; }",
            "  .bounded-footer--frame { margin-left: -20px; margin-right: -20px; }",
            "  .bounded-footer-cluster--end { justify-content: flex-start; }",
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
    viewport_width = int(session_request.get("viewport_width") or 1440)
    viewport_height = int(session_request.get("viewport_height") or 1200)
    blocks = _select_representative_blocks(
        _collect_style_blocks(style_entries),
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    outline: list[dict[str, Any]] = []
    if dom_capture.get("available"):
        _collect_dom_outline(dom_capture.get("content"), outline)

    asset_content = assets_capture.get("content", {}) if isinstance(assets_capture, dict) else {}
    image_count = len(asset_content.get("images", []) or []) if isinstance(asset_content, dict) else 0
    script_count = len(asset_content.get("scripts", []) or []) if isinstance(asset_content, dict) else 0
    iframe_count = len(asset_content.get("iframes", []) or []) if isinstance(asset_content, dict) else 0

    interaction_entries = interactions_capture.get("content", []) if isinstance(interactions_capture, dict) else []
    interaction_sample: list[dict[str, Any]] = []
    for entry in interaction_entries[:24] if isinstance(interaction_entries, list) else []:
        if not isinstance(entry, dict):
            continue
        interaction_sample.append(
            {
                "tag": entry.get("tag"),
                "role": entry.get("role"),
                "kind": entry.get("kind"),
                "text": _clean_text(entry.get("text"), 120) or None,
                "labelText": _clean_text(entry.get("labelText"), 80) or None,
                "label": _clean_text(entry.get("interactionLabel") or entry.get("label"), 80) or None,
                "href": entry.get("href"),
                "type": entry.get("type"),
                "inputCapable": bool(entry.get("inputCapable")),
                "clickCapable": bool(entry.get("clickCapable")),
                "rect": entry.get("rect"),
                "baseStyles": entry.get("baseStyles") if isinstance(entry.get("baseStyles"), dict) else {},
                "targetSummary": entry.get("targetSummary") if isinstance(entry.get("targetSummary"), dict) else {},
                "hoverDeltaKeys": sorted((entry.get("hoverDelta") or {}).keys()) if isinstance(entry.get("hoverDelta"), dict) else [],
                "focusDeltaKeys": sorted((entry.get("focusDelta") or {}).keys()) if isinstance(entry.get("focusDelta"), dict) else [],
                "clickStateDeltaKeys": sorted((((entry.get("clickState") or {}).get("stateDelta")) or {}).keys())
                if isinstance(((entry.get("clickState") or {}).get("stateDelta")), dict)
                else [],
            }
        )

    frame_policy = static.get("frame_policy", {}) if isinstance(static.get("frame_policy", {}), dict) else {}
    meta = static.get("meta", {}) if isinstance(static.get("meta", {}), dict) else {}
    site_profile = static.get("site_profile", {}) if isinstance(static.get("site_profile"), dict) else {}
    route_hints = site_profile.get("route_hints", {}) if isinstance(site_profile.get("route_hints"), dict) else {}
    surface_class = str(site_profile.get("primary_surface") or "static-document")
    breakpoint_summary = capture_bundle.get("breakpoints", {}) if isinstance(capture_bundle, dict) else {}
    breakpoint_variants = breakpoint_summary.get("variants", []) if isinstance(breakpoint_summary, dict) else []
    css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
    typography = _derive_typography(style_entries)
    style_tokens = _derive_style_tokens(style_entries)
    palette = _normalize_palette(_derive_palette(style_entries), css_analysis)
    renderer_kind = "role-inferred-next-app"
    renderer_strategy = "capture-bundle-to-sectioned-app"
    if surface_class in {"js-app-shell-surface", "frame-blocked-app-surface", "authenticated-app-surface"}:
        renderer_kind = "app-shell-dashboard-next-app"
        renderer_strategy = "capture-bundle-to-app-shell"
    elif surface_class == "canvas-or-webgl-surface":
        renderer_kind = "visual-fallback-next-app"
        renderer_strategy = "capture-bundle-to-visual-stage"
    elif surface_class == "multi-frame-document-surface":
        renderer_kind = "frame-aware-next-app"
        renderer_strategy = "capture-bundle-to-frame-aware-app"

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
        "site_profile": site_profile,
        "surface_class": surface_class,
        "route_hints": route_hints,
        "source_signals": static.get("source_signals") or [],
        "candidate_count": len(static.get("candidate_urls") or []),
        "candidate_sample": (static.get("candidate_urls") or [])[:6],
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
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
        "palette": palette,
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
            "kind": renderer_kind,
            "strategy": renderer_strategy,
            "family": renderer_kind,
            "route": route_hints.get("renderer_route"),
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
    app_model = _build_app_model(summary)
    runtime_materialization = _build_runtime_materialization(summary, app_model, style_entries)
    summary["runtimeMaterialization"] = runtime_materialization
    app_model["runtimeMaterialization"] = runtime_materialization
    summary["layoutTokens"] = app_model.get("layoutTokens") or {}
    html = _render_html(summary)
    css = _render_css(summary)
    tsx = _render_tsx(summary)
    prompt = _render_prompt(summary)
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
