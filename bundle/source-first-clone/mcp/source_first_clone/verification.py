"""Fidelity verification scaffolding."""

from __future__ import annotations

from typing import Any


def verify_fidelity_report(
    reference_bundle: dict[str, Any] | None = None,
    candidate_bundle: dict[str, Any] | None = None,
    reference_url: str | None = None,
    candidate_url: str | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": "stub",
        "reference_url": reference_url or (reference_bundle or {}).get("url"),
        "candidate_url": candidate_url or (candidate_bundle or {}).get("url"),
        "message": "Full fidelity verification is not implemented yet. Provide screenshot, DOM, and style artifacts to enable comparison.",
        "checks": [
            "viewport parity",
            "layout alignment",
            "typography parity",
            "interaction-state parity",
            "asset completeness",
        ],
        "missing_artifacts": [
            "reference screenshots",
            "candidate screenshots",
            "DOM snapshots",
            "computed styles",
            "interaction traces",
        ],
    }

