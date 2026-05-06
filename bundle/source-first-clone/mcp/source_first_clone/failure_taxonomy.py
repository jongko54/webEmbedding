"""Operational failure taxonomy for production clone pipelines."""

from __future__ import annotations

from typing import Any


BLOCKING_CODES = {
    "route-inspection-error",
    "missing-primary-surface",
    "missing-renderer-route",
    "blocked-by-policy",
}

SESSION_CODES = {
    "auth-session-missing",
    "public-app-gate",
    "session-storage-export-only",
}

MANUAL_REVIEW_CODES = {
    "canvas-visual-fallback",
    "network-replay-limited",
    "network-request-failures",
    "frame-documents-limited",
    "native-app-target-required",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _add_issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    action: str,
    evidence: str,
) -> None:
    if any(issue.get("code") == code for issue in issues):
        return
    issues.append(
        {
            "code": code,
            "severity": severity,
            "action": action,
            "evidence": evidence,
        }
    )


def network_replay_readiness(network_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Classify whether captured network evidence is ready for replay-oriented use."""

    summary = _as_dict(network_summary)
    request_count = _int(summary.get("requestCount") or summary.get("request_count"))
    response_count = _int(summary.get("responseCount") or summary.get("response_count"))
    failure_count = _int(summary.get("failureCount") or summary.get("failure_count"))
    har_entry_count = _int(
        summary.get("harEntryCount")
        or summary.get("har_entry_count")
        or summary.get("harLikeEntryCount")
        or summary.get("har_like_entry_count")
    )
    body_availability = _as_dict(
        summary.get("responseBodyAvailability") or summary.get("response_body_availability")
    )
    likely_has_body = _int(body_availability.get("likelyHasBody"))
    request_headers = _as_dict(
        summary.get("requestHeaderPresenceSummary") or summary.get("request_header_presence_summary")
    )
    response_headers = _as_dict(
        summary.get("responseHeaderPresenceSummary") or summary.get("response_header_presence_summary")
    )
    status_counts = _as_dict(summary.get("responseStatusCounts") or summary.get("response_status_counts"))
    server_error_count = sum(
        _int(count)
        for status, count in status_counts.items()
        if str(status).startswith("5")
    )
    auth_error_count = sum(
        _int(count)
        for status, count in status_counts.items()
        if str(status) in {"401", "403"}
    )
    rate_limit_count = _int(status_counts.get("429"))

    reasons: list[str] = []
    if request_count <= 0:
        reasons.append("no captured requests")
    if har_entry_count <= 0:
        reasons.append("no HAR entries")
    elif request_count and har_entry_count < request_count:
        reasons.append("HAR entries do not cover every request")
    if request_count and response_count < request_count:
        reasons.append("responses do not cover every request")
    if failure_count:
        reasons.append("failed requests were captured")
    if server_error_count:
        reasons.append("server error responses were captured")
    if auth_error_count:
        reasons.append("auth/permission responses were captured")
    if rate_limit_count:
        reasons.append("rate-limit responses were captured")
    if request_count and likely_has_body and likely_has_body < max(1, int(response_count * 0.75)):
        reasons.append("response body availability looks low")
    if request_count and not request_headers:
        reasons.append("request headers are missing")
    if response_count and not response_headers:
        reasons.append("response headers are missing")

    if request_count <= 0 or har_entry_count <= 0:
        status = "limited"
        next_action = "rerun capture with runtime network tracing before claiming replay parity"
    elif failure_count or auth_error_count or rate_limit_count or server_error_count:
        status = "needs-retry-or-session"
        next_action = "retry with supplied session, longer wait, or explicit network allowlist"
    elif reasons:
        status = "partial"
        next_action = "review HAR gaps before using responses as replay-grade evidence"
    else:
        status = "ready"
        next_action = "network evidence is sufficient for replay-oriented inspection"

    return {
        "status": status,
        "request_count": request_count,
        "response_count": response_count,
        "failure_count": failure_count,
        "har_entry_count": har_entry_count,
        "likely_body_count": likely_has_body,
        "auth_error_count": auth_error_count,
        "rate_limit_count": rate_limit_count,
        "server_error_count": server_error_count,
        "reasons": reasons,
        "next_action": next_action,
    }


def classify_pipeline_failure(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return production routing status and actionable failure classes."""

    payload = _as_dict(context)
    item = _as_dict(payload.get("benchmark_item"))
    profile = _as_dict(payload.get("site_profile") or _as_dict(item.get("inspect")).get("site_profile"))
    route = _as_dict(payload.get("route") or item.get("route"))
    policy = _as_dict(payload.get("policy"))
    runtime = _as_dict(payload.get("runtime"))
    captures = _as_dict(payload.get("captures"))
    capture_summary = _as_dict(payload.get("capture_summary") or _as_dict(item.get("capture")).get("depth_summary"))
    evidence = _as_dict(payload.get("evidence_limitations"))
    profile_warnings = [str(value) for value in _as_list(item.get("profile_warnings"))]
    signals = _as_dict(profile.get("signals"))
    route_hints = _as_dict(profile.get("route_hints"))
    critical_depths = {
        str(value)
        for value in _as_list(route.get("critical_depths") or route_hints.get("critical_depths"))
    }
    surface = str(route.get("primary_surface") or profile.get("primary_surface") or "")
    renderer_route = str(route.get("renderer_route") or route_hints.get("renderer_route") or "")
    evidence_limit = str(route.get("evidence_limit") or route_hints.get("evidence_limit") or "")
    issues: list[dict[str, Any]] = []

    if item.get("status") == "error":
        _add_issue(
            issues,
            code="route-inspection-error",
            severity="blocking",
            action="retry static inspection and persist the exception with URL metadata",
            evidence=str(item.get("error") or "benchmark item status=error"),
        )
    if policy.get("mode") == "blocked":
        _add_issue(
            issues,
            code="blocked-by-policy",
            severity="blocking",
            action="do not clone without updated permission or license evidence",
            evidence=str(policy.get("reason") or "policy mode is blocked"),
        )
    if "missing_primary_surface" in profile_warnings or not surface:
        _add_issue(
            issues,
            code="missing-primary-surface",
            severity="blocking",
            action="improve site_profile classification before routing this URL",
            evidence="primary surface is missing",
        )
    if "missing_renderer_route" in profile_warnings or not renderer_route:
        _add_issue(
            issues,
            code="missing-renderer-route",
            severity="blocking",
            action="add a renderer route fallback before queueing reconstruction",
            evidence="renderer route is missing",
        )
    if evidence_limit == "public-web-app-gate":
        _add_issue(
            issues,
            code="public-app-gate",
            severity="session-required",
            action="request authenticated browser evidence, user screenshots, or native-app target evidence",
            evidence="route evidence_limit=public-web-app-gate",
        )
    if "native-app-deep-links" in critical_depths:
        _add_issue(
            issues,
            code="native-app-target-required",
            severity="manual-review",
            action="do not claim native target fidelity from public web shell alone",
            evidence="critical depth includes native-app-deep-links",
        )
    session = _as_dict(runtime.get("session"))
    session_input = bool(
        session.get("storageStateApplied")
        or session.get("userDataDir")
        or payload.get("storage_state_path")
        or payload.get("user_data_dir")
    )
    storage_exported = bool(session.get("storageStateExported") or _as_dict(payload.get("artifacts")).get("storage_state_exported"))
    if surface == "authenticated-app-surface" and not session_input:
        code = "session-storage-export-only" if storage_exported else "auth-session-missing"
        _add_issue(
            issues,
            code=code,
            severity="session-required",
            action="rerun with user-supplied authenticated storage state or persistent browser profile",
            evidence="authenticated surface has no supplied session input",
        )
    if renderer_route in {"visual-fallback-rebuild", "runtime-first-bounded-rebuild"} and surface == "canvas-or-webgl-surface":
        _add_issue(
            issues,
            code="canvas-visual-fallback",
            severity="manual-review",
            action="verify screenshot-led stage geometry before accepting DOM parity",
            evidence="canvas/WebGL surface uses visual fallback route",
        )

    network_depth = _as_dict(capture_summary.get("network") or captures.get("network"))
    if network_depth:
        readiness = network_replay_readiness(network_depth)
        if readiness.get("failure_count"):
            _add_issue(
                issues,
                code="network-request-failures",
                severity="manual-review",
                action="retry or filter failed requests before replay-grade use",
                evidence=f"{readiness.get('failure_count')} failed requests",
            )
        if readiness.get("status") in {"limited", "partial", "needs-retry-or-session"}:
            _add_issue(
                issues,
                code="network-replay-limited",
                severity="manual-review",
                action=str(readiness.get("next_action")),
                evidence=", ".join(readiness.get("reasons") or []) or str(readiness.get("status")),
            )
    elif "network" in critical_depths:
        _add_issue(
            issues,
            code="network-replay-limited",
            severity="manual-review",
            action="run browser capture with network manifest and HAR export enabled",
            evidence="route requires network depth but no network capture summary is present",
        )

    dom_depth = _as_dict(capture_summary.get("dom") or captures.get("dom"))
    if "frame-documents" in critical_depths and dom_depth and _int(dom_depth.get("frame_document_count")) <= 0:
        _add_issue(
            issues,
            code="frame-documents-limited",
            severity="manual-review",
            action="rerun with frame-aware capture and inspect inaccessible frame count",
            evidence="frame document critical depth has no captured frame documents",
        )

    if evidence.get("confidence") == "limited":
        for missing in _as_list(evidence.get("inferred_or_missing"))[:3]:
            _add_issue(
                issues,
                code="limited-evidence",
                severity="manual-review",
                action="treat generated output as bounded until missing evidence is supplied",
                evidence=str(missing),
            )

    codes = [str(issue.get("code")) for issue in issues]
    if any(code in BLOCKING_CODES for code in codes):
        status = "blocked"
    elif any(code in SESSION_CODES for code in codes):
        status = "needs-session"
    elif any(code in MANUAL_REVIEW_CODES or code == "limited-evidence" for code in codes):
        status = "manual-review"
    else:
        status = "ready"

    return {
        "status": status,
        "codes": codes,
        "issues": issues,
    }
