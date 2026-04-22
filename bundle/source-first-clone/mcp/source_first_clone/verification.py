"""Bounded fidelity verification for capture bundles."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .verification_support import build_fidelity_report


def _normalize_reuse_url(url: Any) -> str:
    if not url:
        return ""
    parsed = urlsplit(str(url).strip())
    netloc = parsed.netloc.lower()
    if parsed.scheme.lower() == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif parsed.scheme.lower() == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _capture_artifact_flags(capture_bundle: dict[str, Any]) -> dict[str, bool]:
    bundle = capture_bundle.get("bundle", {}) if isinstance(capture_bundle, dict) else {}
    artifacts = bundle.get("artifacts", {}) if isinstance(bundle.get("artifacts"), dict) else {}
    keys = (
        "html",
        "screenshot",
        "dom_snapshot",
        "computed_styles",
        "css_analysis",
        "network_manifest",
        "interaction_states",
        "interaction_trace",
    )
    return {key: bool(artifacts.get(key)) for key in keys}


def build_exact_reuse_verification(
    capture_bundle: dict[str, Any],
    exact_candidate: dict[str, Any],
) -> dict[str, Any]:
    static = capture_bundle.get("static", {}) if isinstance(capture_bundle.get("static"), dict) else {}
    runtime = capture_bundle.get("runtime", {}) if isinstance(capture_bundle.get("runtime"), dict) else {}
    frame_policy = static.get("frame_policy", {}) if isinstance(static.get("frame_policy"), dict) else {}
    candidate_url = str(exact_candidate.get("url") or "")
    candidate_norm = _normalize_reuse_url(candidate_url)
    reference_urls = [
        capture_bundle.get("url"),
        static.get("final_url"),
        runtime.get("finalUrl"),
    ]
    reference_norms = sorted({normalized for normalized in (_normalize_reuse_url(url) for url in reference_urls) if normalized})
    artifact_flags = _capture_artifact_flags(capture_bundle)
    has_core_capture = all(
        artifact_flags.get(key)
        for key in ("html", "screenshot", "dom_snapshot", "computed_styles", "network_manifest")
    )
    candidate_matches_reference = bool(candidate_norm and candidate_norm in reference_norms)
    embeddable = frame_policy.get("embeddable") is True
    kind = str(exact_candidate.get("kind") or "")
    source = str(exact_candidate.get("source") or "")

    if kind == "direct-iframe" and candidate_matches_reference and embeddable and has_core_capture:
        status = "source-equivalent"
        confidence = "high"
        ready = True
    elif candidate_norm and has_core_capture and source.startswith(("static", "runtime")):
        status = "captured-candidate"
        confidence = "medium"
        ready = False
    else:
        status = "unverified-candidate"
        confidence = "low"
        ready = False

    notes: list[str] = []
    if status == "source-equivalent":
        notes.append("Exact reuse target is the same frameable source URL that was captured.")
    else:
        notes.append("Exact reuse target was selected from source/runtime candidates, but visual equivalence was not independently replayed.")
    if not has_core_capture:
        notes.append("Core capture evidence is incomplete, so exact reuse confidence is limited.")
    if not embeddable and kind == "direct-iframe":
        notes.append("Frame policy did not prove cross-origin iframe reuse is safe.")

    return {
        "status": status,
        "ready_for_exact_clone": ready,
        "ready_for_exact_reuse": ready,
        "confidence": confidence,
        "candidate_url": candidate_url,
        "candidate_kind": kind,
        "candidate_source": source,
        "candidate_url_matches_reference": candidate_matches_reference,
        "reference_urls": reference_norms,
        "frame_policy_embeddable": embeddable,
        "capture_artifacts": artifact_flags,
        "notes": notes,
    }


def verify_fidelity_report(
    reference_bundle: dict[str, Any] | None = None,
    candidate_bundle: dict[str, Any] | None = None,
    reference_url: str | None = None,
    candidate_url: str | None = None,
) -> dict[str, Any]:
    return build_fidelity_report(
        reference_bundle=reference_bundle,
        candidate_bundle=candidate_bundle,
        reference_url=reference_url,
        candidate_url=candidate_url,
    )
