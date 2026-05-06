#!/usr/bin/env python3
"""Summarize persisted clone benchmark score artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCORE_FILENAMES = {"summary.json", "verification.json", "visual-qa.json", "repair-plan.json"}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def score_fields(payload: dict[str, Any]) -> list[tuple[str, float]]:
    fields: list[tuple[str, float]] = []
    candidates = {
        "score": payload.get("score"),
        "screen_clone_score": payload.get("screen_clone_score"),
        "breakpoint_score_average": payload.get("breakpoint_score_average"),
    }
    comparison_summary = payload.get("comparison_summary")
    if isinstance(comparison_summary, dict):
        candidates["comparison_summary.score"] = comparison_summary.get("score")
    visual_qa = payload.get("visual_qa")
    if isinstance(visual_qa, dict):
        candidates["visual_qa.score"] = visual_qa.get("score")
    preferred_renderer = payload.get("preferred_renderer")
    if isinstance(preferred_renderer, dict):
        candidates["preferred_renderer.score"] = preferred_renderer.get("score")
    for field, value in candidates.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            fields.append((field, float(value)))
    return fields


def find_source_url(payload: dict[str, Any]) -> str:
    for key in ("url", "reference_url", "source_url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    reference = payload.get("reference")
    if isinstance(reference, dict):
        value = reference.get("url")
        if isinstance(value, str) and value:
            return value
    return ""


def iter_score_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in root.rglob("*.json"):
        if path.name not in SCORE_FILENAMES:
            continue
        payload = load_json(path)
        if not payload:
            continue
        status = payload.get("verdict") or payload.get("status") or payload.get("grade") or ""
        source_url = find_source_url(payload)
        for field, score in score_fields(payload):
            rows.append(
                {
                    "score": score,
                    "field": field,
                    "status": status,
                    "source_url": source_url,
                    "path": str(path),
                }
            )
    return sorted(rows, key=lambda row: (float(row["score"]), str(row["path"]), str(row["field"])))


def format_score(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize persisted clone benchmark score artifacts.")
    parser.add_argument(
        "--root",
        default=".tmp",
        help="Artifact root to scan. Defaults to .tmp.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0,
        help="Only include scores greater than or equal to this value.",
    )
    parser.add_argument(
        "--max-score",
        type=float,
        default=100,
        help="Only include scores less than or equal to this value.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of rows to print.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON rows instead of TSV.")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    rows = [
        row
        for row in iter_score_rows(root)
        if float(args.min_score) <= float(row["score"]) <= float(args.max_score)
    ][: max(0, int(args.limit))]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    print("score\tfield\tstatus\tsource_url\tpath")
    for row in rows:
        print(
            "\t".join(
                [
                    format_score(float(row["score"])),
                    str(row["field"]),
                    str(row["status"]),
                    str(row["source_url"]),
                    str(row["path"]),
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
