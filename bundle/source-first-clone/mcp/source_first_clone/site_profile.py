"""Universal site-profile classification and routing hints."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


APP_RUNTIME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nextjs", re.compile(r"__next|_next/static|next-data", re.I)),
    ("nuxt", re.compile(r"__nuxt|_nuxt/", re.I)),
    ("react", re.compile(r"data-reactroot|react-root|react-dom", re.I)),
    ("vue", re.compile(r"data-v-|vue(?:\.runtime)?", re.I)),
    ("svelte", re.compile(r"svelte", re.I)),
    ("angular", re.compile(r"ng-version|angular", re.I)),
    ("shopify", re.compile(r"Shopify\.|window\.Shopify|shopify-section|shopify-features|cdn\.shopify\.com|myshopify\.com", re.I)),
    ("wordpress", re.compile(r"wp-content|wp-includes|wp-json|wordpress", re.I)),
    ("wix", re.compile(r"wixstatic\.com|static\.parastorage\.com|wix-code|wix-thunderbolt|wixsite\.com", re.I)),
    ("squarespace", re.compile(r"static1?\.squarespace\.com|squarespace-cdn\.com|sqspcdn\.com|squarespace-context", re.I)),
    ("super", re.compile(r"super\.site|sites\.super\.so|super-content|super\.so/(?:api|cdn|static)", re.I)),
    ("notion", re.compile(r"notion-static\.com|notion\.site|notion\.so/(?:image|images)", re.I)),
)

AUTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"type=[\"']password[\"']", re.I),
    re.compile(r"sign in|log in|login|member login|account", re.I),
    re.compile(r"oauth|auth0|okta|clerk|supabase", re.I),
)

APP_GATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("app_store_badge", re.compile(r"app store|download on the app store", re.I)),
    ("play_store_badge", re.compile(r"google play|play store|get it on google play", re.I)),
    ("open_in_app", re.compile(r"open in app|view in app|continue in app|use the app|앱에서\s*보기|앱으로\s*보기|앱에서\s*열기", re.I)),
    ("app_download", re.compile(r"download (?:the )?app|app download|install (?:the )?app|앱\s*다운로드|앱\s*설치", re.I)),
    ("login_gate", re.compile(r"login required|sign in required|after login|로그인\s*후|로그인이\s*필요|로그인하고", re.I)),
    ("qr_app_link", re.compile(r"(?:qr code|qrcode|scan (?:the )?qr|qr\s*코드)[\s\S]{0,120}(?:app|download|앱)", re.I)),
    ("deep_link", re.compile(r"intent://|market://|itms-apps://|android-app://|ios-app://|applinks?:", re.I)),
    ("app_link_meta", re.compile(r"al:(?:ios|android):|apple-itunes-app|app-argument", re.I)),
)

CANVAS_LIBRARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwebgl\b", re.I),
    re.compile(r"\bthree(?:\.js)?\b", re.I),
    re.compile(r"\bbabylon(?:\.js)?\b", re.I),
    re.compile(r"\bpixi(?:\.js)?\b", re.I),
    re.compile(r"\blottie(?:-player|web)?\b", re.I),
    re.compile(r"\b(?:@rive-app|rive-canvas|rive-player|rivefile)\b", re.I),
)

SHADOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"attachShadow\s*\(", re.I),
    re.compile(r"shadowroot", re.I),
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _count(pattern: str, html: str) -> int:
    return len(re.findall(pattern, html, re.I))


def _bool_patterns(patterns: tuple[re.Pattern[str], ...], html: str) -> bool:
    return any(pattern.search(html or "") for pattern in patterns)


def _runtime_frameworks(html: str) -> list[str]:
    frameworks: list[str] = []
    for name, pattern in APP_RUNTIME_PATTERNS:
        if pattern.search(html or ""):
            frameworks.append(name)
    return frameworks


def _matched_named_patterns(patterns: tuple[tuple[str, re.Pattern[str]], ...], html: str) -> list[str]:
    matches: list[str] = []
    for name, pattern in patterns:
        if pattern.search(html or ""):
            matches.append(name)
    return matches


def classify_site_profile(
    *,
    final_url: str,
    html: str,
    headers: dict[str, Any] | None,
    frame_policy: dict[str, Any] | None,
    platform_adapter: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
    candidate_urls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    html = html or ""
    headers = headers or {}
    frame_policy = frame_policy or {}
    meta = meta or {}
    platform_adapter = platform_adapter or {}
    candidate_urls = candidate_urls or []
    host = _host(final_url)
    platform = str(platform_adapter.get("platform") or "generic").lower()

    script_count = _count(r"<script\b", html)
    form_count = _count(r"<form\b", html)
    iframe_count = _count(r"<iframe\b", html)
    canvas_count = _count(r"<canvas\b", html)
    section_count = _count(r"<section\b|<article\b|<main\b|<nav\b|<header\b|<footer\b", html)
    paragraph_count = _count(r"<p\b", html)
    heading_count = _count(r"<h[1-6]\b", html)
    link_count = _count(r"<a\b", html)
    list_item_count = _count(r"<li\b", html)

    exact_candidate_kinds = [
        str(item.get("kind") or "").lower()
        for item in candidate_urls
        if isinstance(item, dict) and item.get("kind")
    ]
    exact_candidate_present = any(
        kind in {
            "direct-iframe",
            "spline-preview",
            "spline-viewer",
            "figma-embed",
            "youtube-embed",
            "vimeo-embed",
            "codepen-embed",
            "generic-embed",
            "iframe-src",
        }
        for kind in exact_candidate_kinds
    )

    runtime_frameworks = _runtime_frameworks(html)
    app_gate_signals = _matched_named_patterns(APP_GATE_PATTERNS, html)
    app_deep_link_detected = any(signal in {"deep_link", "app_link_meta"} for signal in app_gate_signals)
    app_promo_detected = any(
        signal in {"app_store_badge", "play_store_badge", "open_in_app", "app_download", "qr_app_link"}
        for signal in app_gate_signals
    )
    app_login_gate_detected = "login_gate" in app_gate_signals
    strong_app_gate_signals = {"open_in_app", "login_gate", "qr_app_link", "deep_link", "app_link_meta"}
    app_gate_detected = any(signal in strong_app_gate_signals for signal in app_gate_signals)
    auth_detected = _bool_patterns(AUTH_PATTERNS, html) or app_login_gate_detected
    canvas_library_detected = _bool_patterns(CANVAS_LIBRARY_PATTERNS, html)
    canvas_detected = canvas_count > 0 or canvas_library_detected
    shadow_dom_detected = _bool_patterns(SHADOW_PATTERNS, html)
    frame_blocked = frame_policy.get("embeddable") is False
    multi_frame = iframe_count > 1
    longform = section_count >= 6 or (paragraph_count >= 12 and heading_count >= 3)
    if not longform and not exact_candidate_present:
        content_hub_density = (
            section_count >= 4
            and heading_count >= 6
            and paragraph_count >= 2
            and (list_item_count >= 20 or link_count >= 40)
        )
        frame_blocked_content_density = (
            frame_blocked
            and heading_count >= 3
            and section_count >= 4
            and (paragraph_count >= 8 or link_count >= 40 or list_item_count >= 20)
        )
        longform = content_hub_density or frame_blocked_content_density
    app_shell = bool(runtime_frameworks) or script_count >= 15
    canvas_dominant = canvas_detected and (
        canvas_library_detected
        or canvas_count >= 2
        or (canvas_count >= 1 and not app_shell and section_count <= 2)
    )
    platform_managed = platform != "generic"

    surface = "static-document"
    confidence = "medium"
    notes: list[str] = []

    if platform_managed:
        surface = "platform-managed-surface"
        confidence = "high"
        notes.append(f"Platform adapter matched `{platform}`.")
    elif canvas_dominant:
        surface = "canvas-or-webgl-surface"
        confidence = "high"
        notes.append("Canvas/WebGL-style runtime was detected.")
    elif app_gate_detected:
        surface = "authenticated-app-surface"
        confidence = "high" if app_deep_link_detected or app_login_gate_detected else "medium"
        notes.append("App download, deep-link, QR, or login-gate signals suggest the public web page is a limited app/auth-gated surface.")
    elif auth_detected and app_shell:
        surface = "authenticated-app-surface"
        confidence = "high"
        notes.append("Authentication signals plus JS app-shell markers were detected.")
    elif frame_blocked and app_shell:
        surface = "frame-blocked-app-surface"
        confidence = "high"
        notes.append("Cross-origin framing is blocked and the page looks app-like.")
    elif multi_frame:
        surface = "multi-frame-document-surface"
        confidence = "medium"
        notes.append("Multiple frame surfaces were detected.")
    elif longform:
        surface = "longform-content-surface"
        confidence = "medium"
        notes.append("Content density suggests a longform document surface.")
    elif app_shell:
        surface = "js-app-shell-surface"
        confidence = "medium"
        notes.append("JS runtime markers dominate the page shell.")

    acquisition_profile = "static-first"
    if surface in {"frame-blocked-app-surface", "js-app-shell-surface"}:
        acquisition_profile = "browser-deep-capture"
    elif surface == "authenticated-app-surface":
        acquisition_profile = "session-aware-browser-capture"
    elif surface == "canvas-or-webgl-surface":
        acquisition_profile = "visual-runtime-capture"
    elif surface == "multi-frame-document-surface":
        acquisition_profile = "frame-aware-capture"
    elif platform_managed:
        acquisition_profile = "platform-aware-source-first"

    renderer_route = "bounded-rebuild"
    if exact_candidate_present and not app_gate_detected:
        renderer_route = "exact-reuse"
    elif platform_managed:
        renderer_route = "platform-source-or-bounded-rebuild"
    elif surface == "canvas-or-webgl-surface":
        renderer_route = "visual-fallback-rebuild"
    elif surface in {"frame-blocked-app-surface", "authenticated-app-surface", "js-app-shell-surface"}:
        renderer_route = "runtime-first-bounded-rebuild"

    critical_depths = ["dom", "computed-styles", "interactions"]
    if frame_blocked or app_shell:
        critical_depths.extend(["runtime-html", "network"])
    if app_gate_detected:
        critical_depths.extend(["runtime-html", "network", "session-state", "app-gate-evidence", "native-app-deep-links"])
    if shadow_dom_detected:
        critical_depths.append("shadow-dom")
    if multi_frame:
        critical_depths.append("frame-documents")
    if canvas_detected:
        critical_depths.append("canvas-surface")
    if auth_detected:
        critical_depths.append("session-state")

    renderer_family = "document-next-app"
    if surface in {"js-app-shell-surface", "frame-blocked-app-surface", "authenticated-app-surface"}:
        renderer_family = "app-shell-dashboard-next-app"
    elif surface == "canvas-or-webgl-surface":
        renderer_family = "visual-fallback-next-app"
    elif surface == "multi-frame-document-surface":
        renderer_family = "frame-aware-document-next-app"

    route_hints: dict[str, Any] = {
        "acquisition_profile": acquisition_profile,
        "renderer_route": renderer_route,
        "renderer_family": renderer_family,
        "critical_depths": list(dict.fromkeys(critical_depths)),
    }
    if app_gate_detected:
        route_hints["evidence_limit"] = "public-web-app-gate"
        route_hints["evidence_note"] = (
            "Public HTML may only expose app promotion, login, QR, store, or deep-link entry points; "
            "the product/trading UI may require an authenticated browser session or native app evidence."
        )

    return {
        "version": 1,
        "host": host,
        "platform": platform,
        "primary_surface": surface,
        "confidence": confidence,
        "signals": {
            "platform_managed": platform_managed,
            "frame_blocked": frame_blocked,
            "app_shell": app_shell,
            "auth_detected": auth_detected,
            "app_gate_detected": app_gate_detected,
            "app_deep_link_detected": app_deep_link_detected,
            "app_promo_detected": app_promo_detected,
            "app_login_gate_detected": app_login_gate_detected,
            "canvas_detected": canvas_detected,
            "canvas_dominant": canvas_dominant,
            "shadow_dom_detected": shadow_dom_detected,
            "multi_frame": multi_frame,
            "longform": longform,
            "script_count": script_count,
            "form_count": form_count,
            "iframe_count": iframe_count,
            "canvas_count": canvas_count,
            "section_count": section_count,
            "paragraph_count": paragraph_count,
            "heading_count": heading_count,
            "link_count": link_count,
            "list_item_count": list_item_count,
            "runtime_frameworks": runtime_frameworks,
            "app_gate_signals": app_gate_signals,
            "exact_candidate_present": exact_candidate_present,
            "exact_candidate_kinds": exact_candidate_kinds[:12],
        },
        "route_hints": route_hints,
        "notes": notes,
        "meta": {
            "title": meta.get("og:title") or meta.get("title"),
            "canonical_hint": meta.get("og:url"),
            "description_present": bool(meta.get("description") or meta.get("og:description")),
            "headers_seen": sorted(str(key).lower() for key in headers.keys())[:20],
        },
    }
