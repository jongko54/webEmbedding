"""Execution planning for source-first clone workflows."""

from __future__ import annotations

from typing import Any

from .policy import classify_clone_mode


def plan_reproduction_path(
    exact_requested: bool = True,
    license_text: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    source_signals: list[str] | None = None,
    capture_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = classify_clone_mode(
        exact_requested=exact_requested,
        license_text=license_text,
        candidates=candidates,
        source_signals=source_signals,
    )

    mode = policy["mode"]
    plan: list[dict[str, str]] = [
        {"stage": "policy", "action": f"confirm {mode} path"},
        {"stage": "acquisition", "action": "collect any missing source, preview, embed, or session state URLs"},
        {"stage": "capture", "action": "build a richer capture bundle with DOM, styles, assets, and session-aware runtime artifacts"},
        {"stage": "execution", "action": "choose embed, source import, or rebuild"},
        {"stage": "verification", "action": "compare fidelity across breakpoints and interactions"},
    ]

    if capture_bundle is None:
        capture_bundle_state = "missing"
    else:
        capture_bundle_state = "present"

    return {
        "mode": mode,
        "policy": policy,
        "capture_bundle_state": capture_bundle_state,
        "plan": plan,
        "required_artifacts": [
            "source url",
            "preview url",
            "session context or storage state",
            "DOM snapshot",
            "computed styles",
            "screenshot set",
            "interaction trace",
        ],
        "note": "This is planning only; it does not execute any cloning or rewrite step.",
    }
