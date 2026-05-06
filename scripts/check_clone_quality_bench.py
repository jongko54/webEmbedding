#!/usr/bin/env python3
"""Run a lightweight clone-quality benchmark and print compact score rows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URLS = [
    "https://developer.mozilla.org/en-US/",
    "https://www.example.com",
]
DEFAULT_BREAKPOINTS = ["mobile", "tablet"]


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_json_text(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def slugify_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path or "url").lower()
    host = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    path_bits = [
        bit
        for bit in re.split(r"[/]+", parsed.path.strip("/"))
        if bit
    ]
    path_slug = "-".join(re.sub(r"[^a-z0-9]+", "-", bit.lower()).strip("-") for bit in path_bits[:3])
    slug = "-".join(part for part in (host, path_slug) if part)
    return slug or "url"


def fmt_number(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def fmt_yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def fmt_list(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "-"
    return ",".join(str(item) for item in values[:4])


def print_row(*columns: str) -> None:
    print(" | ".join(columns))


def run_clone(
    url: str,
    output_dir: Path,
    *,
    wait_seconds: int,
    timeout_seconds: int,
    breakpoints: list[str],
) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "node",
        "./bin/web-embedding.mjs",
        "clone",
        "--url",
        url,
        "--output-dir",
        str(output_dir),
        "--wait-seconds",
        str(wait_seconds),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if breakpoints:
        command.extend(["--breakpoints", *breakpoints])
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def summary_score(summary: dict[str, Any]) -> Any:
    comparison_summary = summary.get("comparison_summary")
    if isinstance(comparison_summary, dict) and isinstance(comparison_summary.get("score"), (int, float)):
        return comparison_summary.get("score")
    if isinstance(summary.get("score"), (int, float)):
        return summary.get("score")
    return "-"


def summary_ready(summary: dict[str, Any]) -> Any:
    for key in ("overall_ready_for_exact_clone", "exact_ready", "ready"):
        value = summary.get(key)
        if isinstance(value, bool):
            return value
    root_report = summary.get("root_report")
    if isinstance(root_report, dict) and isinstance(root_report.get("ready_for_exact_clone"), bool):
        return root_report.get("ready_for_exact_clone")
    return None


def load_self_verify_summary(output_dir: Path) -> dict[str, Any] | None:
    return load_json(output_dir / "reproduction" / "self-verify" / "summary.json")


def iter_renderer_rows(summary_dir: Path, self_verify: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    renderers = self_verify.get("renderers")
    persisted = self_verify.get("persisted", {})
    persisted_renderers = persisted.get("renderers") if isinstance(persisted, dict) else {}

    if isinstance(renderers, list) and renderers:
        for renderer in renderers:
            if not isinstance(renderer, dict):
                continue
            name = str(renderer.get("name") or "renderer")
            row = {
                "name": name,
                "kind": renderer.get("kind"),
                "score": renderer.get("score"),
                "ready": renderer.get("ready_for_exact_clone"),
                "report_path": renderer.get("report_path"),
                "visual_qa_path": None,
            }
            persisted_entry = persisted_renderers.get(name) if isinstance(persisted_renderers, dict) else None
            if isinstance(persisted_entry, dict):
                row["report_path"] = persisted_entry.get("root_report") or row["report_path"]
                row["visual_qa_path"] = persisted_entry.get("visual_qa")
            rows.append(row)
        return rows

    renderers_dir = summary_dir / "renderers"
    if not renderers_dir.is_dir():
        return rows
    for renderer_dir in sorted(path for path in renderers_dir.iterdir() if path.is_dir()):
        report_path = renderer_dir / "verification.json"
        visual_qa_path = renderer_dir / "visual-qa.json"
        rows.append(
            {
                "name": renderer_dir.name,
                "kind": None,
                "score": None,
                "ready": None,
                "report_path": str(report_path) if report_path.is_file() else None,
                "visual_qa_path": str(visual_qa_path) if visual_qa_path.is_file() else None,
            }
        )
    return rows


def summarize_renderer_row(row: dict[str, Any], summary_dir: Path) -> str:
    name = str(row.get("name") or "renderer")
    verification = load_json(Path(str(row["report_path"]))) if row.get("report_path") else None
    visual_qa = load_json(Path(str(row["visual_qa_path"]))) if row.get("visual_qa_path") else None

    score = row.get("score")
    if not isinstance(score, (int, float)) and isinstance(verification, dict):
        if isinstance(verification.get("comparison_summary"), dict) and isinstance(verification["comparison_summary"].get("score"), (int, float)):
            score = verification["comparison_summary"]["score"]
        elif isinstance(verification.get("score"), (int, float)):
            score = verification["score"]

    ready = row.get("ready")
    if not isinstance(ready, bool) and isinstance(verification, dict) and isinstance(verification.get("ready_for_exact_clone"), bool):
        ready = verification["ready_for_exact_clone"]
    if not isinstance(ready, bool) and isinstance(verification, dict):
        root_report = verification.get("root_report")
        if isinstance(root_report, dict) and isinstance(root_report.get("ready_for_exact_clone"), bool):
            ready = root_report["ready_for_exact_clone"]

    visual_bits = []
    if isinstance(visual_qa, dict):
        visual_bits.append(f"visual={str(visual_qa.get('grade') or 'unknown')}@{fmt_number(visual_qa.get('score'))}")
        if visual_qa.get("drift_flags"):
            visual_bits.append(f"drift={fmt_list(visual_qa.get('drift_flags'))}")
    else:
        visual_bits.append("visual=-")

    verifier = "-"
    if isinstance(verification, dict):
        verifier = f"verify={str(verification.get('verdict') or verification.get('status') or 'unknown')}@{fmt_number(score)}"
    elif isinstance(score, (int, float)):
        verifier = f"score={fmt_number(score)}"

    ready_cell = f"ready={fmt_yes_no(ready)}"
    return f"{name} | {verifier} | {ready_cell} | {' '.join(visual_bits)}"


def print_summary_row(url: str, summary: dict[str, Any], output_dir: Path) -> None:
    coverage = str(summary.get("coverage") or summary.get("policy_mode") or "-")
    next_action = str(summary.get("next_action") or "-")
    score = fmt_number(summary_score(summary))
    screen_score = fmt_number(summary.get("screen_clone_score"))
    breakpoint_count = fmt_number(summary.get("breakpoint_ready_count"))
    breakpoint_average = fmt_number(summary.get("breakpoint_score_average"))
    ready = fmt_yes_no(summary_ready(summary))
    root_report = summary.get("root_report") if isinstance(summary.get("root_report"), dict) else {}
    comparison_summary = summary.get("comparison_summary") if isinstance(summary.get("comparison_summary"), dict) else {}
    verdict = str(root_report.get("verdict") or comparison_summary.get("verdict") or "-")
    print_row(
        url,
        f"coverage={coverage}",
        f"next={next_action}",
        f"score={score}",
        f"screen={screen_score}",
        f"bp_ready={breakpoint_count}",
        f"bp_avg={breakpoint_average}",
        f"ready={ready}",
        f"verdict={verdict}",
    )
    breakpoints = summary.get("breakpoints") if isinstance(summary.get("breakpoints"), dict) else {}
    for report in breakpoints.get("reports") or []:
        if not isinstance(report, dict):
            continue
        visual_qa = report.get("visual_qa") if isinstance(report.get("visual_qa"), dict) else {}
        print_row(
            f"  breakpoint={report.get('name') or '-'}",
            f"score={fmt_number(report.get('score'))}",
            f"ready={fmt_yes_no(report.get('ready_for_exact_clone'))}",
            f"visual={fmt_number(visual_qa.get('score'))}",
            f"drift={fmt_list(visual_qa.get('drift_flags'))}",
        )

    self_verify_dir = output_dir / "reproduction" / "self-verify"
    for renderer_row in iter_renderer_rows(self_verify_dir, summary):
        print_row(f"  {summarize_renderer_row(renderer_row, self_verify_dir)}")


def print_clone_output_row(url: str, payload: dict[str, Any]) -> None:
    coverage = str(payload.get("coverage") or payload.get("policy_mode") or "-")
    next_action = str(payload.get("next_action") or "-")
    exact_reuse = payload.get("exact_reuse") if isinstance(payload.get("exact_reuse"), dict) else {}
    verification = exact_reuse.get("verification") if isinstance(exact_reuse, dict) else {}
    score = payload.get("score")
    if not isinstance(score, (int, float)) and isinstance(payload.get("comparison_summary"), dict):
        comp = payload.get("comparison_summary")
        if isinstance(comp.get("score"), (int, float)):
            score = comp.get("score")
    ready = None
    for key in ("exact_ready", "ready"):
        value = payload.get(key)
        if isinstance(value, bool):
            ready = value
            break
    if ready is None and isinstance(verification, dict):
        ready = verification.get("ready_for_exact_clone")
        if not isinstance(ready, bool):
            ready = verification.get("ready_for_exact_reuse")
    verdict = "-"
    if isinstance(verification, dict):
        verdict = str(verification.get("status") or verification.get("verdict") or "-")
    print_row(
        url,
        f"coverage={coverage}",
        f"next={next_action}",
        f"score={fmt_number(score)}",
        f"ready={fmt_yes_no(ready)}",
        f"verdict={verdict}",
    )


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def quality_gate_failures(
    summary: dict[str, Any],
    *,
    min_score: float | None,
    min_screen_score: float | None,
    min_breakpoint_average: float | None,
    require_ready: bool,
) -> list[str]:
    failures: list[str] = []
    score = _numeric(summary_score(summary))
    screen_score = _numeric(summary.get("screen_clone_score"))
    breakpoint_average = _numeric(summary.get("breakpoint_score_average"))
    ready = summary_ready(summary)
    if min_score is not None and (score is None or score < min_score):
        failures.append(f"score {fmt_number(score)} < {fmt_number(min_score)}")
    if min_screen_score is not None and (screen_score is None or screen_score < min_screen_score):
        failures.append(f"screen {fmt_number(screen_score)} < {fmt_number(min_screen_score)}")
    if min_breakpoint_average is not None and (breakpoint_average is None or breakpoint_average < min_breakpoint_average):
        failures.append(f"bp_avg {fmt_number(breakpoint_average)} < {fmt_number(min_breakpoint_average)}")
    if require_ready and ready is not True:
        failures.append(f"ready {fmt_yes_no(ready)} != yes")
    return failures


def run_case(
    url: str,
    output_root: Path,
    *,
    wait_seconds: int,
    timeout_seconds: int,
    breakpoints: list[str],
    min_score: float | None,
    min_screen_score: float | None,
    min_breakpoint_average: float | None,
    require_ready: bool,
) -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{slugify_url(url)}-{timestamp}"
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)

    result = run_clone(
        url,
        case_dir,
        wait_seconds=wait_seconds,
        timeout_seconds=timeout_seconds,
        breakpoints=breakpoints,
    )
    if result.returncode != 0:
        print_row(url, "ERROR", f"clone failed with code {result.returncode}")
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    clone_payload = load_json_text(result.stdout)
    summary = load_self_verify_summary(case_dir)
    if not isinstance(summary, dict):
        if isinstance(clone_payload, dict):
            print_clone_output_row(url, clone_payload)
            return 0
        print_row(url, "ERROR", "missing reproduction/self-verify/summary.json")
        return 1

    print_summary_row(url, summary, case_dir)
    failures = quality_gate_failures(
        summary,
        min_score=min_score,
        min_screen_score=min_screen_score,
        min_breakpoint_average=min_breakpoint_average,
        require_ready=require_ready,
    )
    for failure in failures:
        print_row(url, "GATE_FAIL", failure)
    if failures:
        return 1
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "urls",
        nargs="*",
        help="URLs to clone. If omitted, a small default set is used.",
    )
    parser.add_argument(
        "--output-root",
        default=str(ROOT / ".tmp" / "clone-quality-bench"),
        help="Directory under which per-URL clone outputs are created.",
    )
    parser.add_argument("--wait-seconds", type=int, default=2, help="Browser settle time passed to the clone command.")
    parser.add_argument("--timeout-seconds", type=int, default=35, help="Static fetch timeout passed to the clone command.")
    parser.add_argument(
        "--breakpoints",
        nargs="*",
        choices=["mobile", "tablet", "desktop"],
        default=DEFAULT_BREAKPOINTS,
        help="Additional breakpoint profiles to capture. Defaults to mobile/tablet; primary capture remains desktop-sized.",
    )
    parser.add_argument(
        "--no-breakpoints",
        action="store_true",
        help="Run only the primary viewport.",
    )
    parser.add_argument("--min-score", type=float, help="Fail if the root self-verify score is below this value.")
    parser.add_argument("--min-screen-score", type=float, help="Fail if the screen/visual score is below this value.")
    parser.add_argument("--min-breakpoint-average", type=float, help="Fail if breakpoint average is below this value.")
    parser.add_argument("--require-ready", action="store_true", help="Fail unless the summary is ready for exact clone/reuse.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    urls = args.urls or DEFAULT_URLS
    breakpoints = [] if args.no_breakpoints else list(args.breakpoints or [])
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    for url in urls:
        exit_code = max(
            exit_code,
            run_case(
                url,
                output_root,
                wait_seconds=max(1, int(args.wait_seconds)),
                timeout_seconds=max(1, int(args.timeout_seconds)),
                breakpoints=breakpoints,
                min_score=args.min_score,
                min_screen_score=args.min_screen_score,
                min_breakpoint_average=args.min_breakpoint_average,
                require_ready=bool(args.require_ready),
            ),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
