"""Platform-aware adapter hints for source-first clone workflows."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse


SPLINE_FILE_RE = re.compile(r"https://app\.spline\.design/file/[^\"'\s>]+", re.I)
SPLINE_VIEWER_RE = re.compile(r"https://viewer\.spline\.design/[^\"'\s>]+", re.I)
FIGMA_DUPLICATE_RE = re.compile(r"duplicate\s+this\s+file", re.I)
WEBFLOW_ASSET_RE = re.compile(r"(webflow|website-files\.com|webflow\.io)", re.I)
FRAMER_ASSET_RE = re.compile(r"(framerusercontent|framer\.website|framer\.app|framer\.com)", re.I)
FIGMA_PATH_RE = re.compile(r"^/(?:community/file|file|proto|design|board|slides)/", re.I)


def _dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not url or not kind:
            continue
        key = (kind, url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def inspect_platform_adapter(
    final_url: str,
    html: str,
    meta: dict[str, str] | None = None,
) -> dict[str, Any]:
    lowered_url = (final_url or "").lower()
    lowered_html = (html or "").lower()
    generator = _extract_generator(html)
    meta = meta or {}

    adapter: dict[str, Any] = {
        "platform": "generic",
        "confidence": "low",
        "generator": generator,
        "source_signals": [],
        "candidates": [],
        "notes": [],
    }

    if "spline.design" in lowered_url or "spline.design" in lowered_html:
        adapter.update(_inspect_spline(final_url, html))
    elif "figma.com" in lowered_url or "figma.com" in lowered_html:
        adapter.update(_inspect_figma(final_url, html))
    elif _looks_like_framer(final_url, generator, lowered_html):
        adapter.update(_inspect_framer(final_url, generator))
    elif _looks_like_webflow(final_url, generator, lowered_html):
        adapter.update(_inspect_webflow(final_url, generator))
    else:
        adapter["notes"].append("No platform-specific adapter matched; generic candidate extraction remains active.")

    adapter["candidates"] = _dedupe_candidates(adapter.get("candidates", []))
    adapter["source_signals"] = list(dict.fromkeys(str(signal).lower() for signal in adapter.get("source_signals", []) if signal))
    if meta.get("og:url") and meta.get("og:url") != final_url:
        adapter["notes"].append("og:url differs from the fetched URL; downstream code should prefer the canonical target when appropriate.")
    return adapter


def merge_platform_candidates(
    base_candidates: list[dict[str, Any]] | None,
    adapter_candidates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    combined = list(base_candidates or [])
    for item in adapter_candidates or []:
        if isinstance(item, dict):
            combined.append(item)
    return _dedupe_candidates(combined)


def _extract_generator(html: str) -> str | None:
    match = re.search(
        r"<meta[^>]+(?:name|property)=[\"']generator[\"'][^>]+content=[\"']([^\"']+)",
        html or "",
        re.I,
    )
    if not match:
        return None
    return " ".join(match.group(1).split())


def _looks_like_framer(final_url: str, generator: str | None, lowered_html: str) -> bool:
    host = (urlparse(final_url).hostname or "").lower()
    generator_text = (generator or "").lower()
    return (
        host.endswith(".framer.website")
        or host.endswith(".framer.app")
        or "framer" in generator_text
        or bool(FRAMER_ASSET_RE.search(lowered_html))
    )


def _looks_like_webflow(final_url: str, generator: str | None, lowered_html: str) -> bool:
    host = (urlparse(final_url).hostname or "").lower()
    generator_text = (generator or "").lower()
    return (
        host.endswith(".webflow.io")
        or "webflow" in generator_text
        or bool(WEBFLOW_ASSET_RE.search(lowered_html))
    )


def _inspect_spline(final_url: str, html: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    source_signals: list[str] = []
    lowered_html = (html or "").lower()

    if "/community/file/" in final_url.lower():
        source_signals.append("remix")
        notes.append("Spline community file detected; remix/source reuse is often available when licensing allows it.")
    if "licensed under cc0" in lowered_html or "cc0 1.0" in lowered_html:
        source_signals.append("cc0")
        notes.append("Spline page mentions CC0 licensing.")

    for match in SPLINE_FILE_RE.findall(html or ""):
        url = match.strip()
        if "view=preview" not in url.lower():
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}view=preview"
        candidates.append({"kind": "spline-preview", "url": url, "source": "platform-adapter", "platform": "spline"})
    for match in SPLINE_VIEWER_RE.findall(html or ""):
        candidates.append({"kind": "spline-viewer", "url": match.strip(), "source": "platform-adapter", "platform": "spline"})

    if "/file/" in final_url.lower() and "view=preview" not in final_url.lower():
        separator = "&" if "?" in final_url else "?"
        candidates.append(
            {
                "kind": "spline-preview",
                "url": f"{final_url}{separator}view=preview",
                "source": "platform-adapter",
                "platform": "spline",
            }
        )
        notes.append("Generated a Spline preview URL from a raw file link.")

    return {
        "platform": "spline",
        "confidence": "high",
        "source_signals": source_signals,
        "candidates": candidates,
        "notes": notes or ["Spline-specific preview/viewer extraction is active."],
    }


def _inspect_figma(final_url: str, html: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    source_signals: list[str] = []
    lowered_url = final_url.lower()
    lowered_html = (html or "").lower()
    parsed = urlparse(final_url)
    path = parsed.path or ""

    if "figma.com/embed" in lowered_url:
        candidates.append({"kind": "figma-embed", "url": final_url, "source": "platform-adapter", "platform": "figma"})
        notes.append("Existing Figma embed URL detected.")
    elif FIGMA_PATH_RE.search(path):
        embed_url = f"https://www.figma.com/embed?embed_host=share&url={quote(final_url, safe='')}"
        candidates.append({"kind": "figma-embed", "url": embed_url, "source": "platform-adapter", "platform": "figma"})
        notes.append("Generated a Figma embed URL from the original share link.")
    if "/community/" in lowered_url or FIGMA_DUPLICATE_RE.search(lowered_html):
        source_signals.append("duplicate")
        notes.append("Figma community or duplicate signal detected.")
    if "/proto/" in lowered_url:
        notes.append("Prototype-style Figma URL detected; embed should preserve the interactive prototype surface.")
    if "/design/" in lowered_url or "/file/" in lowered_url:
        notes.append("Design file URL detected; embed should preserve the live file surface.")
    if "/board/" in lowered_url:
        notes.append("FigJam board URL detected.")
    if "/slides/" in lowered_url:
        notes.append("Figma Slides URL detected.")

    return {
        "platform": "figma",
        "confidence": "high",
        "source_signals": source_signals,
        "candidates": candidates,
        "notes": notes or ["Figma embed generation is active."],
    }


def _inspect_framer(final_url: str, generator: str | None) -> dict[str, Any]:
    notes = ["Framer publish surface detected."]
    if generator:
        notes.append(f"Generator meta: {generator}")
    return {
        "platform": "framer",
        "confidence": "medium",
        "source_signals": [],
        "candidates": [],
        "notes": notes,
    }


def _inspect_webflow(final_url: str, generator: str | None) -> dict[str, Any]:
    notes = ["Webflow publish surface detected."]
    if generator:
        notes.append(f"Generator meta: {generator}")
    if urlparse(final_url).hostname and urlparse(final_url).hostname.lower().endswith(".webflow.io"):
        notes.append("webflow.io host suggests a publish preview rather than a custom production domain.")
    return {
        "platform": "webflow",
        "confidence": "medium",
        "source_signals": [],
        "candidates": [],
        "notes": notes,
    }
