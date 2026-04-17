"""Clone policy and mode classification."""

from __future__ import annotations

from typing import Any


def classify_clone_mode(
    exact_requested: bool = True,
    license_text: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    source_signals: list[str] | None = None,
    site_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    license_text = (license_text or "").lower()
    source_signals = [str(item).lower() for item in (source_signals or [])]
    candidates = candidates or []
    site_profile = site_profile or {}

    candidate_kinds = {str(item.get("kind", "")).lower() for item in candidates if isinstance(item, dict)}
    reusable_license = any(token in license_text for token in ["cc0", "creative commons", "mit", "apache", "remix"])
    blocked_license = any(token in license_text for token in ["all rights reserved", "copyright"])
    source_signal = any(token in source_signals for token in ["remix", "export", "source", "fork", "duplicate", "cc0"])
    primary_surface = str(site_profile.get("primary_surface") or "").lower()
    route_hints = site_profile.get("route_hints", {}) if isinstance(site_profile.get("route_hints"), dict) else {}
    acquisition_profile = str(route_hints.get("acquisition_profile") or "").lower()

    if blocked_license and not reusable_license:
        return {
            "mode": "blocked",
            "reason": "The provided license text suggests the reference should not be cloned exactly without permission.",
        }

    if candidate_kinds & {"direct-iframe", "spline-preview", "spline-viewer", "iframe-src", "generic-embed", "figma-embed", "youtube-embed", "vimeo-embed", "codepen-embed"}:
        return {
            "mode": "embed",
            "reason": "An original preview, viewer, or iframe candidate exists.",
        }

    if reusable_license or source_signal or "spline-code" in candidate_kinds or "readymag-html-snippet" in candidate_kinds:
        return {
            "mode": "source",
            "reason": "The reference shows remix, export, or source-level reuse signals.",
        }

    if primary_surface in {"canvas-or-webgl-surface", "authenticated-app-surface"}:
        return {
            "mode": "rebuild",
            "reason": "The site profile requires deep runtime capture and bounded rebuild instead of a simple exact-reuse path.",
        }

    if exact_requested:
        return {
            "mode": "rebuild",
            "reason": (
                "No exact reuse path was found, so the request should be rebuilt with a clear accuracy disclaimer."
                if not acquisition_profile
                else f"No exact reuse path was found; proceed with `{acquisition_profile}` and a bounded rebuild."
            ),
        }

    return {"mode": "rebuild", "reason": "A rebuild is appropriate for this reference."}
