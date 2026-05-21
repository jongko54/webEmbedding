"""Safe preflight audit helpers for source-first clone workflows."""

from __future__ import annotations

import re
from typing import Any

from .acquisition import inspect_reference
from .policy import classify_clone_mode


BLOCKED_SIGNAL_RE = re.compile(
    r"(bypass|captcha|paywall|no[-_ ]?permission|unauthori[sz]ed|forbidden|credential|password|steal)",
    re.I,
)
RESTRICTED_SIGNAL_RE = re.compile(
    r"(private|auth|login|account|admin|checkout|payment|session|dashboard)",
    re.I,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        item = str(value or "").strip()
        lowered = item.lower()
        if not item or lowered in seen:
            continue
        seen.add(lowered)
        results.append(item)
    return results


def _candidate_kinds(candidates: list[dict[str, Any]]) -> list[str]:
    return _unique_strings([candidate.get("kind") for candidate in candidates if isinstance(candidate, dict)])


def _has_exact_candidate(candidates: list[dict[str, Any]]) -> bool:
    exact_kinds = {
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
    return any(str(candidate.get("kind") or "").lower() in exact_kinds for candidate in candidates if isinstance(candidate, dict))


def _signal_matches(signals: list[str], pattern: re.Pattern[str]) -> list[str]:
    return [signal for signal in signals if pattern.search(signal)]


def _command(argv: list[str]) -> dict[str, Any]:
    return {"argv": argv, "shell": " ".join(argv)}


def _local_commands(url: str) -> dict[str, dict[str, Any]]:
    return {
        "inspect": _command(["npx", "-y", "web-embedding@latest", "inspect", "--url", url]),
        "clone": _command(
            [
                "npx",
                "-y",
                "web-embedding@latest",
                "clone",
                "--url",
                url,
                "--output-dir",
                "./webembedding-output",
            ]
        ),
    }


def audit_reference_url(
    url: str,
    timeout_seconds: int = 20,
    exact_requested: bool = True,
    license_text: str | None = None,
    source_signals: list[str] | None = None,
) -> dict[str, Any]:
    """Return a low-risk routing and approval preflight without browser capture."""

    inspection = inspect_reference(url, timeout_seconds=timeout_seconds)
    candidates = [
        candidate
        for candidate in _as_list(inspection.get("candidate_urls"))
        if isinstance(candidate, dict)
    ]
    site_profile = _as_dict(inspection.get("site_profile"))
    signals = _as_dict(site_profile.get("signals"))
    route_hints = _as_dict(site_profile.get("route_hints"))
    frame_policy = _as_dict(inspection.get("frame_policy"))
    merged_source_signals = _unique_strings(
        [
            *(_as_list(source_signals) if source_signals else []),
            *(_as_list(inspection.get("source_signals"))),
        ]
    )
    policy = classify_clone_mode(
        exact_requested=exact_requested,
        license_text=license_text,
        candidates=candidates,
        source_signals=merged_source_signals,
        site_profile=site_profile,
    )

    blockers: list[str] = []
    approvals_required: list[str] = []
    warnings: list[str] = []
    next_actions: list[str] = []
    recommended_tools = ["inspect_url"]

    blocked_signals = _signal_matches(merged_source_signals, BLOCKED_SIGNAL_RE)
    restricted_signals = _signal_matches(merged_source_signals, RESTRICTED_SIGNAL_RE)
    if policy.get("mode") == "blocked":
        blockers.append(str(policy.get("reason") or "policy classified this target as blocked"))
    if blocked_signals:
        blockers.append("source signals indicate bypass, captcha, paywall, permission, or credential risk")

    primary_surface = str(site_profile.get("primary_surface") or "")
    renderer_route = str(route_hints.get("renderer_route") or "")
    acquisition_profile = str(route_hints.get("acquisition_profile") or "")
    evidence_limit = str(route_hints.get("evidence_limit") or "")
    exact_candidate = _has_exact_candidate(candidates)
    frame_blocked = frame_policy.get("embeddable") is False or bool(signals.get("frame_blocked"))
    session_required = (
        primary_surface == "authenticated-app-surface"
        or bool(signals.get("auth_detected"))
        or bool(signals.get("app_gate_detected"))
        or evidence_limit == "public-web-app-gate"
        or bool(restricted_signals)
    )
    manual_review = (
        bool(signals.get("canvas_detected"))
        or bool(signals.get("multi_frame"))
        or "native-app-deep-links" in {str(item) for item in _as_list(route_hints.get("critical_depths"))}
    )

    if session_required:
        approvals_required.append("Confirm authorization to access this target and provide user-owned session evidence only when needed.")
        recommended_tools.append("detect_runtime_capabilities")
    if frame_blocked or renderer_route in {"bounded-rebuild", "runtime-first-bounded-rebuild", "visual-fallback-rebuild"}:
        approvals_required.append("Approve local browser capture and an output directory before running clone/rebuild tools.")
        recommended_tools.append("clone_reference_url")
    if manual_review:
        approvals_required.append("Review bounded evidence manually before claiming exact fidelity.")
    if policy.get("mode") in {"embed", "source"} and exact_candidate and not session_required:
        recommended_tools.append("generate_embed_snippet")

    if blockers:
        decision = "blocked"
        risk_level = "blocked"
        next_actions.append("Do not capture, clone, or rebuild until permission, license, and access-control issues are resolved.")
    elif session_required:
        decision = "needs_session"
        risk_level = "needs_approval"
        next_actions.append("Ask the user for explicit authorization and user-supplied storage_state_path or user_data_dir before local capture.")
    elif manual_review:
        decision = "manual_review"
        risk_level = "needs_approval"
        next_actions.append("Run local capture only with approval, then verify visual/runtime evidence before accepting the rebuild.")
    elif policy.get("mode") in {"embed", "source"} and exact_candidate:
        decision = "ready_for_exact_or_embed_reuse"
        risk_level = "low"
        next_actions.append("Prefer the original embed/source route and verify frame/source permission before generating code.")
    else:
        decision = "local_capture_recommended"
        risk_level = "low"
        next_actions.append("Run local browser capture and bounded self-verification before rebuilding.")

    if frame_blocked:
        warnings.append("Cross-origin iframe reuse appears blocked; exact iframe embedding may fail.")
    if evidence_limit:
        warnings.append(str(route_hints.get("evidence_note") or f"Evidence is limited by {evidence_limit}."))

    recommended_tools = _unique_strings(recommended_tools)
    hosted_safe = decision in {"ready_for_exact_or_embed_reuse", "local_capture_recommended", "manual_review", "needs_session", "blocked"}
    local_required = decision in {"local_capture_recommended", "needs_session", "manual_review"}

    return {
        "schema_version": 1,
        "url": url,
        "final_url": inspection.get("final_url"),
        "status": inspection.get("status"),
        "title": inspection.get("title"),
        "platform": inspection.get("platform"),
        "decision": decision,
        "risk_level": risk_level,
        "hosted_intake_safe": hosted_safe,
        "requires_local_mcp": local_required,
        "policy": policy,
        "frame_policy": frame_policy,
        "site_profile": {
            "primary_surface": site_profile.get("primary_surface"),
            "confidence": site_profile.get("confidence"),
            "route_hints": route_hints,
            "signals": {
                "frame_blocked": signals.get("frame_blocked"),
                "auth_detected": signals.get("auth_detected"),
                "app_gate_detected": signals.get("app_gate_detected"),
                "canvas_detected": signals.get("canvas_detected"),
                "multi_frame": signals.get("multi_frame"),
                "exact_candidate_present": signals.get("exact_candidate_present"),
                "exact_candidate_kinds": signals.get("exact_candidate_kinds"),
            },
            "notes": site_profile.get("notes"),
        },
        "candidate_summary": {
            "count": len(candidates),
            "kinds": _candidate_kinds(candidates),
            "sample": candidates[:8],
        },
        "approvals_required": _unique_strings(approvals_required),
        "blockers": _unique_strings(blockers),
        "warnings": _unique_strings(warnings),
        "recommended_tools": recommended_tools,
        "next_actions": _unique_strings(next_actions),
        "local_commands": _local_commands(str(inspection.get("final_url") or url)),
        "reviewer_notes": [
            "Hosted intake is read-only and does not run Playwright, read local files, or use browser session state.",
            "Full capture, rebuild, HAR replay, and authenticated runs remain local-first and require caller-selected paths/session evidence.",
        ],
        "source_signals": merged_source_signals,
        "acquisition_profile": acquisition_profile,
    }
