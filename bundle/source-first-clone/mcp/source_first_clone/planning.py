"""Execution planning for source-first clone workflows."""

from __future__ import annotations

from typing import Any

from .policy import classify_clone_mode


def plan_reproduction_path(
    exact_requested: bool = True,
    license_text: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    source_signals: list[str] | None = None,
    site_profile: dict[str, Any] | None = None,
    capture_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = classify_clone_mode(
        exact_requested=exact_requested,
        license_text=license_text,
        candidates=candidates,
        source_signals=source_signals,
        site_profile=site_profile,
    )

    mode = policy["mode"]
    site_profile = site_profile or {}
    route_hints = site_profile.get("route_hints", {}) if isinstance(site_profile.get("route_hints"), dict) else {}
    surface_class = str(site_profile.get("primary_surface") or "unknown")
    acquisition_profile = str(route_hints.get("acquisition_profile") or "static-first")
    renderer_route = str(route_hints.get("renderer_route") or ("exact-reuse" if mode == "embed" else "bounded-rebuild"))
    critical_depths = route_hints.get("critical_depths") if isinstance(route_hints.get("critical_depths"), list) else []
    renderer_family = "document-next-app"
    if surface_class in {"js-app-shell-surface", "frame-blocked-app-surface", "authenticated-app-surface"}:
        renderer_family = "app-shell-dashboard-next-app"
    elif surface_class == "canvas-or-webgl-surface":
        renderer_family = "visual-fallback-next-app"
    elif surface_class == "multi-frame-document-surface":
        renderer_family = "frame-aware-document-next-app"
    app_shell_like = surface_class in {"js-app-shell-surface", "authenticated-app-surface", "frame-blocked-app-surface"}
    plan: list[dict[str, str]] = [
        {"stage": "policy", "action": f"confirm {mode} path"},
        {"stage": "routing", "action": f"classify `{surface_class}` and follow `{acquisition_profile}` -> `{renderer_route}` via `{renderer_family}`"},
        {"stage": "acquisition", "action": "collect any missing source, preview, embed, session, frame, or runtime URLs"},
        {"stage": "capture", "action": "build a richer capture bundle with DOM, styles, assets, network, and session-aware runtime artifacts"},
        {"stage": "execution", "action": "choose embed, source import, runtime-first rebuild, or visual fallback rebuild"},
        {"stage": "verification", "action": "compare fidelity across breakpoints and interactions"},
    ]
    if app_shell_like:
        plan.insert(
            3,
            {
                "stage": "renderer",
                "action": "prefer an app-shell renderer family that preserves toolbar, sidebar, workspace, and inspector topology before landing-page compression",
            },
        )

    required_artifacts = [
        "source url",
        "preview url",
        "session context or storage state",
        "DOM snapshot",
        "computed styles",
        "screenshot set",
        "interaction trace",
    ]
    if surface_class == "static-document":
        required_artifacts = [
            "source url",
            "preview url",
            "DOM snapshot",
            "computed styles",
            "runtime HTML",
            "interaction trace",
        ]
    elif surface_class in {"js-app-shell-surface", "frame-blocked-app-surface"}:
        required_artifacts = [
            "runtime HTML",
            "DOM snapshot",
            "computed styles",
            "network manifest",
            "interaction trace",
            "session context or storage state",
            "breakpoint variants",
        ]
    elif surface_class == "authenticated-app-surface":
        required_artifacts = [
            "runtime HTML",
            "network manifest",
            "session context or storage state",
            "interaction trace",
            "breakpoint variants",
        ]
    if app_shell_like:
        required_artifacts.append("shell topology / panel summary")
    elif surface_class == "canvas-or-webgl-surface":
        required_artifacts = [
            "runtime HTML",
            "viewport screenshot set",
            "network manifest",
            "interaction trace",
            "session context or storage state",
        ]
    elif surface_class == "multi-frame-document-surface":
        required_artifacts = [
            "DOM snapshot",
            "frame document summaries",
            "computed styles",
            "interaction trace",
            "network manifest",
        ]

    if capture_bundle is None:
        capture_bundle_state = "missing"
    else:
        capture_bundle_state = "present"

    visual_fallback: dict[str, Any] | None = None
    if surface_class == "canvas-or-webgl-surface":
        visual_fallback = {
            "available": True,
            "renderer_route": "visual-fallback-rebuild",
            "renderer_family": "visual-fallback-next-app",
            "rendering_model": "full-viewport-stage-with-overlay-chrome",
            "strategy": "Capture the live visual composition, then rebuild a bounded HTML/CSS/Next scaffold that preserves stage geometry, dominant colors, text hierarchy, and stable interaction affordances.",
            "rendering_constraints": [
                "Treat the canvas/WebGL area as the primary stage, not as an inspectable DOM subtree.",
                "Rebuild overlay chrome, captions, and controls as bounded HTML/CSS around the stage.",
                "Use screenshots and runtime HTML to preserve composition before attempting DOM-level fidelity.",
                "Keep responsive variants aligned to the captured viewport geometry.",
            ],
            "scaffold_hints": [
                "single-stage layout with overlay chrome",
                "bounded caption and control rails",
                "viewport-anchored stage geometry",
                "screenshot-led composition checks",
                "DOM and CSS fidelity layered after the visual pass",
            ],
            "capture_hints": [
                "viewport screenshot set",
                "runtime HTML and DOM outline",
                "computed styles for visible overlays and controls",
                "asset inventory for images, scripts, and iframes",
                "interaction trace for hover, focus, click, and scroll-state changes",
                "canvas or WebGL element counts and bounding boxes when present",
                "breakpoint variants for desktop, tablet, and mobile",
            ],
            "fallback_focus": [
                "stage sizing",
                "palette and contrast",
                "text hierarchy",
                "overlay controls",
                "viewport-specific composition",
            ],
            "verification_hints": [
                "stage bounds align to the captured viewport",
                "overlay controls stay visible and reachable",
                "palette and contrast remain consistent with the reference",
                "responsive variants keep the same composition hierarchy",
                "screenshot similarity is the primary check before DOM detail",
                "stage, chrome, and caption layers remain separable in the rebuild",
            ],
        }

    return {
        "mode": mode,
        "policy": policy,
        "site_profile": site_profile,
        "surface_class": surface_class,
        "route_hints": {
            "acquisition_profile": acquisition_profile,
            "renderer_route": renderer_route,
            "renderer_family": renderer_family,
            "critical_depths": critical_depths,
        },
        "capture_bundle_state": capture_bundle_state,
        "plan": plan,
        "required_artifacts": required_artifacts,
        "visual_fallback": visual_fallback,
        "note": "This is planning only; it does not execute any cloning or rewrite step.",
    }
