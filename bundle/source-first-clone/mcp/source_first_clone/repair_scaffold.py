"""Bounded auto-repair pass for scaffold artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .rebuild_scaffold import _render_bounded_reference_page_html, _render_reference_data_ts


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
        click_state = entry.get("clickState") or {}
        click_delta = click_state.get("stateDelta") if isinstance(click_state, dict) else {}
        if isinstance(hover_delta, dict) and hover_delta:
            states.append(f"hover: {', '.join(sorted(str(key) for key in hover_delta.keys()))}")
        if isinstance(focus_delta, dict) and focus_delta:
            states.append(f"focus: {', '.join(sorted(str(key) for key in focus_delta.keys()))}")
        if isinstance(click_delta, dict) and click_delta:
            states.append(f"click: {', '.join(sorted(str(key) for key in click_delta.keys()))}")
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
    return bits[:limit]


def _repair_css(
    base_css: str,
    app_model: dict[str, Any],
    repair_plan: dict[str, Any],
    capture_bundle: dict[str, Any],
) -> str:
    palette = app_model.get("palette", {}) if isinstance(app_model.get("palette", {}), dict) else {}
    typography = app_model.get("typography", {}) if isinstance(app_model.get("typography", {}), dict) else {}
    viewport = app_model.get("viewport", {}) if isinstance(app_model.get("viewport", {}), dict) else {}
    focus_checks = repair_plan.get("focus_checks", []) if isinstance(repair_plan, dict) else []
    focus_checks = [str(item) for item in focus_checks if item]
    viewport_width = int(viewport.get("width") or 1440)
    shell_width = max(360, min(viewport_width - 48, 1320))
    font_family = ((typography.get("fonts") or ["Inter, system-ui, sans-serif"])[0]) if isinstance(typography.get("fonts", []), list) else "Inter, system-ui, sans-serif"
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
                ".bounded-hero h1, .bounded-card h2, .bounded-brand { font-family: var(--bounded-font-sans); }",
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
    css_analysis = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis", {}), dict) else {}
    css_content = css_analysis.get("content", {}) if isinstance(css_analysis, dict) else {}
    body_style = css_content.get("bodyComputedStyle", {}) if isinstance(css_content.get("bodyComputedStyle", {}), dict) else {}
    if body_style.get("backgroundColor") or body_style.get("color"):
        lines.append("body {")
        if body_style.get("backgroundColor"):
            lines.append(f"  background-color: {body_style['backgroundColor']};")
        if body_style.get("color"):
            lines.append(f"  color: {body_style['color']};")
        lines.append("}")
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
    css_analysis_capture = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis", {}), dict) else {}
    css_analysis = css_analysis_capture.get("content", {}) if isinstance(css_analysis_capture, dict) else {}
    body_style = css_analysis.get("bodyComputedStyle", {}) if isinstance(css_analysis.get("bodyComputedStyle", {}), dict) else {}
    root_style = css_analysis.get("rootComputedStyle", {}) if isinstance(css_analysis.get("rootComputedStyle", {}), dict) else {}
    palette = repaired_app_model.get("palette", {}) if isinstance(repaired_app_model.get("palette", {}), dict) else {}
    typography = repaired_app_model.get("typography", {}) if isinstance(repaired_app_model.get("typography", {}), dict) else {}

    if body_style.get("color"):
        palette["text"] = body_style.get("color")
        applied_repairs.append("Promoted body text color from live CSS analysis into bounded palette tokens.")
    if body_style.get("backgroundColor"):
        palette["surface"] = body_style.get("backgroundColor")
        applied_repairs.append("Promoted body background color from live CSS analysis into bounded palette tokens.")
    if root_style.get("backgroundColor") and not palette.get("surfaceAlt"):
        palette["surfaceAlt"] = root_style.get("backgroundColor")
    if body_style.get("fontFamily"):
        fonts = typography.get("fonts", []) if isinstance(typography.get("fonts", []), list) else []
        fonts = [body_style.get("fontFamily"), *[item for item in fonts if item != body_style.get("fontFamily")]]
        typography["fonts"] = fonts[:3]
        applied_repairs.append("Promoted live body font family into bounded typography tokens.")

    repaired_interactions = _interaction_cards_from_capture(capture_bundle, limit=10)
    if repaired_interactions:
        repaired_app_model["interactions"] = repaired_interactions
        applied_repairs.append("Expanded interaction cards from the full runtime interaction capture.")

    trace_bits = _trace_signal_bits(capture_bundle, limit=4)
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
    reconstruction["repairFocus"] = focus_checks[:4]
    reconstruction["repairSource"] = repair_plan.get("target_renderer")
    repaired_app_model["reconstruction"] = reconstruction

    hero = repaired_app_model.get("hero", {}) if isinstance(repaired_app_model.get("hero", {}), dict) else {}
    hero["eyebrow"] = "Auto-repaired reconstruction"
    hero_details = hero.get("details", []) if isinstance(hero.get("details", []), list) else []
    for finding in (repair_plan.get("priority_findings") or [])[:3]:
        if not isinstance(finding, dict):
            continue
        _append_unique(hero_details, finding.get("focus"))
    hero["details"] = hero_details[:6]
    repaired_app_model["hero"] = hero
    repaired_app_model["palette"] = palette
    repaired_app_model["typography"] = typography

    repaired_summary["signals"] = json.loads(json.dumps(repaired_summary.get("signals", {})))
    repaired_summary["signals"]["auto_repair_available"] = True
    repaired_summary["repairPass"] = {
        "target_renderer": repair_plan.get("target_renderer"),
        "focus_checks": focus_checks[:4],
        "applied_repairs": applied_repairs[:8],
        "recommended_actions": (repair_plan.get("recommended_actions") or [])[:6],
    }

    base_css = _read_text(rebuild_artifacts.get("next-app/app/globals.css"))
    repaired_css = _repair_css(base_css, repaired_app_model, repair_plan, capture_bundle)
    repaired_preview = _render_bounded_reference_page_html(repaired_app_model)
    repaired_data_ts = _render_reference_data_ts(repaired_app_model)

    artifacts: dict[str, Any] = {
        "layout-summary.json": repaired_summary,
        "app-model.json": repaired_app_model,
        "app-preview.html": repaired_preview,
        "next-app/app/globals.css": repaired_css,
        "next-app/components/reference-data.ts": repaired_data_ts,
        "prompt.txt": str(repair_plan.get("prompt") or "").rstrip() + "\n",
        "repair-notes.json": {
            "target_renderer": repair_plan.get("target_renderer"),
            "focus_checks": focus_checks[:4],
            "applied_repairs": applied_repairs[:8],
            "priority_findings": (repair_plan.get("priority_findings") or [])[:6],
            "recommended_actions": (repair_plan.get("recommended_actions") or [])[:6],
        },
    }

    for key in (
        "starter.html",
        "starter.css",
        "starter.tsx",
        "next-app/app/layout.tsx",
        "next-app/app/page.tsx",
        "next-app/components/BoundedReferencePage.tsx",
    ):
        content = _read_text(rebuild_artifacts.get(key))
        if content:
            artifacts[key] = content

    artifacts["manifest.json"] = {
        "schema_version": "repair-pass.v1",
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
