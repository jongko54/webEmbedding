"""Bounded auto-repair pass for scaffold artifacts."""

from __future__ import annotations

import json
import re
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


def _compact_body_sections(
    summary: dict[str, Any],
    links: list[dict[str, Any]],
    trace_bits: list[str],
) -> list[dict[str, Any]]:
    title = _clean_text(summary.get("title"), 56) or "Captured reference"
    assets = summary.get("assets", {}) if isinstance(summary.get("assets", {}), dict) else {}
    sections: list[dict[str, Any]] = []
    if links:
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
            }
        )
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
        }
    )
    return sections[:2]


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
    presentation = app_model.get("presentation", {}) if isinstance(app_model.get("presentation", {}), dict) else {}
    compact = str(presentation.get("variant") or "") == "compact-center-stage"
    if body_style.get("backgroundColor") or body_style.get("color"):
        lines.append("body {")
        if body_style.get("backgroundColor"):
            lines.append(f"  background-color: {body_style['backgroundColor']};")
        if body_style.get("color"):
            lines.append(f"  color: {body_style['color']};")
        lines.append("}")
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
    unique_links = _unique_link_entries(capture_bundle, limit=8)
    trace_bits = _trace_signal_bits(capture_bundle, limit=4)
    outline_sample = _outline_text_sample(base_summary, limit=3)

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
    if unique_links:
        masthead = repaired_app_model.get("masthead", {}) if isinstance(repaired_app_model.get("masthead", {}), dict) else {}
        masthead["links"] = unique_links[:6]
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
    reconstruction["repairFocus"] = focus_checks[:4]
    reconstruction["repairSource"] = repair_plan.get("target_renderer")
    repaired_app_model["reconstruction"] = reconstruction

    hero = repaired_app_model.get("hero", {}) if isinstance(repaired_app_model.get("hero", {}), dict) else {}
    hero["eyebrow"] = "Auto-repaired reconstruction"
    if unique_links:
        hero["actions"] = [{"label": item["label"], "href": item["href"], "states": []} for item in unique_links[:3]]
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

    if "dom snapshot" in focus_checks or "screenshot" in focus_checks:
        repaired_app_model["bodySections"] = _compact_body_sections(base_summary, unique_links, trace_bits)
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
        "next-app/components/BoundedReferencePage.tsx": _render_repaired_bounded_reference_page_component(),
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
