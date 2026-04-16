"""Platform-aware adapter hints for source-first clone workflows."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse


SPLINE_FILE_RE = re.compile(r"https://app\.spline\.design/file/[^\"'\s>]+", re.I)
SPLINE_VIEWER_RE = re.compile(r"https://viewer\.spline\.design/[^\"'\s>]+", re.I)
FIGMA_DUPLICATE_RE = re.compile(r"duplicate\s+this\s+file", re.I)
FIGMA_PATH_RE = re.compile(r"^/(?:community/file|file|proto|design|board|slides)/", re.I)
FRAMER_PUBLISH_HOST_RE = re.compile(r"(?:^|\.)framer\.(?:app|website)$", re.I)
FRAMER_RUNTIME_RE = re.compile(r"(?:data-framer|__framer|framer-embed)", re.I)
FRAMER_ASSET_HINT_RE = re.compile(r"framerusercontent", re.I)
WEBFLOW_PUBLISH_HOST_RE = re.compile(r"(?:^|\.)webflow\.io$", re.I)
WEBFLOW_RUNTIME_RE = re.compile(r"(data-wf-site|data-wf-page|webflow\.js|cdn\.prod\.website-files\.com|assets\.website-files\.com)", re.I)


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


def _append_unique(items: list[str], value: str | None) -> None:
    if not value:
        return
    if value not in items:
        items.append(value)


def _append_candidate(candidates: list[dict[str, Any]], kind: str, url: str, platform: str, source: str = "platform-adapter") -> None:
    cleaned_url = str(url or "").strip()
    if not cleaned_url:
        return
    candidates.append({"kind": kind, "url": cleaned_url, "source": source, "platform": platform})


def _host(final_url: str) -> str:
    return (urlparse(final_url).hostname or "").lower()


def _is_publish_host(host: str, pattern: re.Pattern[str]) -> bool:
    return bool(host and pattern.search(host))


def _looks_like_page_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    lowered_path = (parsed.path or "").lower()
    if lowered_path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".css", ".js", ".map", ".woff", ".woff2", ".ico", ".json")):
        return False
    return True


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
        adapter.update(_inspect_framer(final_url, html, generator))
    elif _looks_like_webflow(final_url, generator, lowered_html):
        adapter.update(_inspect_webflow(final_url, html, generator))
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
    host = _host(final_url)
    generator_text = (generator or "").lower()
    return (
        _is_publish_host(host, FRAMER_PUBLISH_HOST_RE)
        or "framer" in generator_text
        or bool(FRAMER_RUNTIME_RE.search(lowered_html))
        or bool(FRAMER_ASSET_HINT_RE.search(lowered_html))
    )


def _looks_like_webflow(final_url: str, generator: str | None, lowered_html: str) -> bool:
    host = _host(final_url)
    generator_text = (generator or "").lower()
    return (
        _is_publish_host(host, WEBFLOW_PUBLISH_HOST_RE)
        or "webflow" in generator_text
        or bool(WEBFLOW_RUNTIME_RE.search(lowered_html))
        or bool(re.search(r"website-files\.com", lowered_html, re.I))
    )


def _inspect_spline(final_url: str, html: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    source_signals: list[str] = []
    lowered_html = (html or "").lower()
    lowered_url = final_url.lower()
    parsed = urlparse(final_url)
    host = parsed.hostname or ""
    path = parsed.path or ""

    if "/community/file/" in final_url.lower():
        source_signals.append("remix")
        _append_unique(source_signals, "community")
        notes.append("Spline community file detected; remix/source reuse is often available when licensing allows it.")
    if "licensed under cc0" in lowered_html or "cc0 1.0" in lowered_html:
        source_signals.append("cc0")
        notes.append("Spline page mentions CC0 licensing.")

    if _looks_like_page_url(final_url) and (host.endswith(".spline.design") or host == "app.spline.design"):
        if "/file/" in path and "view=preview" not in lowered_url:
            separator = "&" if "?" in final_url else "?"
            _append_candidate(candidates, "spline-preview", f"{final_url}{separator}view=preview", "spline")
            notes.append("Generated a Spline preview URL from a raw file link.")
        if "/community/file/" in path and "view=preview" not in lowered_url:
            separator = "&" if "?" in final_url else "?"
            _append_candidate(candidates, "spline-preview", f"{final_url}{separator}view=preview", "spline")
            notes.append("Generated a Spline preview URL from a community file link.")

    if host == "viewer.spline.design" and _looks_like_page_url(final_url):
        _append_candidate(candidates, "spline-viewer", final_url, "spline")
        notes.append("Spline viewer URL detected.")

    for match in SPLINE_FILE_RE.findall(html or ""):
        url = match.strip()
        if "view=preview" not in url.lower():
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}view=preview"
        if _looks_like_page_url(url):
            _append_candidate(candidates, "spline-preview", url, "spline")
    for match in SPLINE_VIEWER_RE.findall(html or ""):
        url = match.strip()
        if _looks_like_page_url(url):
            _append_candidate(candidates, "spline-viewer", url, "spline")

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
    host = parsed.hostname or ""
    path = parsed.path or ""

    if "figma.com/embed" in lowered_url and host.endswith("figma.com") and _looks_like_page_url(final_url):
        _append_candidate(candidates, "figma-embed", final_url, "figma")
        notes.append("Existing Figma embed URL detected.")
    elif host.endswith("figma.com") and FIGMA_PATH_RE.search(path) and _looks_like_page_url(final_url):
        embed_url = f"https://www.figma.com/embed?embed_host=share&url={quote(final_url, safe='')}"
        _append_candidate(candidates, "figma-embed", embed_url, "figma")
        notes.append("Generated a Figma embed URL from the original share link.")
    if "/community/" in lowered_url or FIGMA_DUPLICATE_RE.search(lowered_html):
        _append_unique(source_signals, "duplicate")
        _append_unique(source_signals, "community")
        notes.append("Figma community or duplicate signal detected.")
    if "/proto/" in lowered_url:
        _append_unique(source_signals, "prototype")
        notes.append("Prototype-style Figma URL detected; embed should preserve the interactive prototype surface.")
    if "/design/" in lowered_url or "/file/" in lowered_url:
        notes.append("Design file URL detected; embed should preserve the live file surface.")
    if "/board/" in lowered_url:
        _append_unique(source_signals, "board")
        notes.append("FigJam board URL detected.")
    if "/slides/" in lowered_url:
        _append_unique(source_signals, "slides")
        notes.append("Figma Slides URL detected.")

    return {
        "platform": "figma",
        "confidence": "high",
        "source_signals": source_signals,
        "candidates": candidates,
        "notes": notes or ["Figma embed generation is active."],
    }


def _inspect_framer(final_url: str, html: str, generator: str | None) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    lowered_url = final_url.lower()
    lowered_html = (html or "").lower()
    host = _host(final_url)
    notes = ["Framer publish surface detected."]
    source_signals: list[str] = []
    publish_host = _is_publish_host(host, FRAMER_PUBLISH_HOST_RE)
    runtime_signal = bool(FRAMER_RUNTIME_RE.search(lowered_html))
    asset_signal = bool(FRAMER_ASSET_HINT_RE.search(lowered_html))

    if publish_host:
        notes.append("Framer-managed host detected.")
        _append_unique(source_signals, "published-host")
        _append_unique(source_signals, "framer")

    if generator:
        notes.append(f"Generator meta: {generator}")
        notes.append("Generator metadata suggests a published Framer surface, even on a custom domain.")
        _append_unique(source_signals, "generator")

    if runtime_signal:
        notes.append("Framer runtime markers were found in the page HTML.")
        _append_unique(source_signals, "runtime")

    if asset_signal:
        notes.append("Framer asset/CDN markers were found in the HTML.")
        _append_unique(source_signals, "asset-backed")
        _append_unique(source_signals, "runtime")

    if "framerusercontent" in lowered_html:
        notes.append("framerusercontent assets suggest a published Framer surface backed by Framer-hosted assets.")
        _append_unique(source_signals, "export-like")

    if "/embed" in lowered_url or "embed=" in lowered_url or "embed_host=" in lowered_url:
        notes.append("Embed-shaped Framer URL detected.")
        _append_unique(source_signals, "embed-like")

    if (publish_host or generator or runtime_signal) and _looks_like_page_url(final_url):
        _append_candidate(candidates, "direct-iframe", final_url, "framer")
        notes.append("Promoted the fetched Framer URL as a direct iframe candidate.")

    return {
        "platform": "framer",
        "confidence": "high" if publish_host else "medium",
        "source_signals": source_signals,
        "candidates": candidates,
        "notes": notes or ["Framer publish surface detected."],
    }


def _inspect_webflow(final_url: str, html: str, generator: str | None) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    lowered_url = final_url.lower()
    lowered_html = (html or "").lower()
    host = _host(final_url)
    notes = ["Webflow publish surface detected."]
    source_signals: list[str] = []
    publish_host = _is_publish_host(host, WEBFLOW_PUBLISH_HOST_RE)
    runtime_signal = bool(WEBFLOW_RUNTIME_RE.search(lowered_html))

    if publish_host:
        notes.append("Webflow-managed host detected.")
        _append_unique(source_signals, "published-host")
        _append_unique(source_signals, "webflow")

    if generator:
        notes.append(f"Generator meta: {generator}")
        notes.append("Generator metadata suggests a published Webflow surface, even on a custom domain.")
        _append_unique(source_signals, "generator")

    if runtime_signal:
        notes.append("Webflow runtime markers were found in the page HTML.")
        _append_unique(source_signals, "runtime")

    if "/embed" in lowered_url or "embed=" in lowered_url or "embed-code" in lowered_html:
        notes.append("Embed-shaped Webflow URL or embed code marker detected.")
        _append_unique(source_signals, "embed-like")

    if (publish_host or generator or runtime_signal) and _looks_like_page_url(final_url):
        _append_candidate(candidates, "direct-iframe", final_url, "webflow")
        notes.append("Promoted the fetched Webflow URL as a direct iframe candidate.")

    if publish_host:
        notes.append("webflow.io host suggests a publish preview rather than a custom production domain.")
    return {
        "platform": "webflow",
        "confidence": "high" if publish_host else "medium",
        "source_signals": source_signals,
        "candidates": candidates,
        "notes": notes,
    }
