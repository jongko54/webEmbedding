"""Bounded fidelity verification for capture bundles."""

from __future__ import annotations

from typing import Any

from .verification_support import build_fidelity_report


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
