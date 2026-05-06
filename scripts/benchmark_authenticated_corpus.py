#!/usr/bin/env python3
"""Run authenticated-dashboard clone corpus cases from a JSON manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / ".tmp" / "authenticated-dashboard-corpus"
DEFAULT_REPORT_NAME = "authenticated-dashboard-corpus-report.json"
ENV_PATTERN = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")
SECRET_ARG_FLAGS = {"--storage-state-path", "--user-data-dir", "--storage-state-output-path"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def slugify(value: str) -> str:
    parsed = urlparse(value)
    raw = parsed.netloc or parsed.path or value or "case"
    if parsed.path and parsed.netloc:
        raw = f"{parsed.netloc}-{parsed.path.strip('/')}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return slug[:80] or "case"


def ensure_dict(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"{label} must be an object")
    return {}


def ensure_string(value: Any, label: str, errors: list[str], *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    errors.append(f"{label} must be a non-empty string")
    return None


def ensure_int(value: Any, label: str, errors: list[str], *, minimum: int = 1) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    if value < minimum:
        errors.append(f"{label} must be >= {minimum}")
        return None
    return value


def ensure_number(value: Any, label: str, errors: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} must be a number")
        return None
    return float(value)


def ensure_string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label} must be an array of strings")
        return []
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        values.append(item.strip())
    return values


def validate_selector(value: Any, label: str, errors: list[str]) -> dict[str, Any] | None:
    if isinstance(value, str) and value.strip():
        return {"selector": value.strip(), "min_count": 1}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a selector string or object")
        return None
    selector = ensure_string(value.get("selector"), f"{label}.selector", errors, required=True)
    min_count = ensure_int(value.get("min_count", 1), f"{label}.min_count", errors, minimum=0)
    max_count = ensure_int(value.get("max_count"), f"{label}.max_count", errors, minimum=0)
    text_contains = ensure_string(value.get("text_contains"), f"{label}.text_contains", errors)
    if max_count is not None and min_count is not None and max_count < min_count:
        errors.append(f"{label}.max_count must be >= min_count")
    if not selector:
        return None
    normalized: dict[str, Any] = {"selector": selector, "min_count": min_count if min_count is not None else 1}
    if max_count is not None:
        normalized["max_count"] = max_count
    if text_contains:
        normalized["text_contains"] = text_contains
    return normalized


def normalize_scores(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    scores = ensure_dict(value, label, errors)
    normalized: dict[str, Any] = {}
    for key in ("min_score", "min_screen_score", "min_breakpoint_average"):
        score = ensure_number(scores.get(key), f"{label}.{key}", errors)
        if score is not None:
            normalized[key] = score
    if scores.get("require_ready") is not None:
        if isinstance(scores.get("require_ready"), bool):
            normalized["require_ready"] = scores["require_ready"]
        else:
            errors.append(f"{label}.require_ready must be a boolean")
    return normalized


def validate_manifest(payload: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    manifest = ensure_dict(payload, "manifest", errors)
    normalized: dict[str, Any] = {
        "schema_version": manifest.get("schema_version", 1),
        "corpus_name": ensure_string(manifest.get("corpus_name"), "corpus_name", errors) or "authenticated-dashboard-corpus",
        "defaults": {},
        "items": [],
    }
    if normalized["schema_version"] != 1:
        errors.append("schema_version must be 1")

    defaults = ensure_dict(manifest.get("defaults", {}), "defaults", errors)
    normalized_defaults = normalized["defaults"]
    for key in ("wait_seconds", "timeout_seconds", "viewport_width", "viewport_height"):
        value = ensure_int(defaults.get(key), f"defaults.{key}", errors)
        if value is not None:
            normalized_defaults[key] = value
    viewport = defaults.get("viewport")
    if viewport is not None:
        viewport_dict = ensure_dict(viewport, "defaults.viewport", errors)
        width = ensure_int(viewport_dict.get("width"), "defaults.viewport.width", errors)
        height = ensure_int(viewport_dict.get("height"), "defaults.viewport.height", errors)
        if width is not None:
            normalized_defaults["viewport_width"] = width
        if height is not None:
            normalized_defaults["viewport_height"] = height
    normalized_defaults["breakpoints"] = ensure_string_list(defaults.get("breakpoints"), "defaults.breakpoints", errors)
    normalized_defaults["source_signals"] = ensure_string_list(defaults.get("source_signals"), "defaults.source_signals", errors)
    normalized_defaults["scores"] = normalize_scores(defaults.get("scores"), "defaults.scores", errors)
    for bool_key in ("skip_runtime_trace", "skip_html", "skip_screenshot", "not_exact"):
        if defaults.get(bool_key) is not None:
            if isinstance(defaults.get(bool_key), bool):
                normalized_defaults[bool_key] = defaults[bool_key]
            else:
                errors.append(f"defaults.{bool_key} must be a boolean")

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty array")
        return normalized, errors

    seen_ids: set[str] = set()
    for index, raw_item in enumerate(items):
        label = f"items[{index}]"
        item = ensure_dict(raw_item, label, errors)
        url = ensure_string(item.get("url"), f"{label}.url", errors, required=True)
        item_id = ensure_string(item.get("id"), f"{label}.id", errors) or (slugify(url or f"case-{index + 1}") if url else f"case-{index + 1}")
        if item_id in seen_ids:
            errors.append(f"{label}.id must be unique: {item_id}")
        seen_ids.add(item_id)
        session = ensure_dict(item.get("session", {}), f"{label}.session", errors)
        expected = ensure_dict(item.get("expected", {}), f"{label}.expected", errors)
        selectors: list[dict[str, Any]] = []
        raw_selectors = expected.get("selectors", item.get("expected_selectors", []))
        if raw_selectors is not None:
            if not isinstance(raw_selectors, list):
                errors.append(f"{label}.expected.selectors must be an array")
            else:
                for selector_index, selector_value in enumerate(raw_selectors):
                    selector = validate_selector(selector_value, f"{label}.expected.selectors[{selector_index}]", errors)
                    if selector:
                        selectors.append(selector)
        item_scores = normalize_scores(expected.get("scores", item.get("expected_scores")), f"{label}.expected.scores", errors)
        for score_key in ("min_score", "min_screen_score", "min_breakpoint_average"):
            score = ensure_number(item.get(score_key), f"{label}.{score_key}", errors)
            if score is not None:
                item_scores[score_key] = score
        if item.get("require_ready") is not None:
            if isinstance(item.get("require_ready"), bool):
                item_scores["require_ready"] = item["require_ready"]
            else:
                errors.append(f"{label}.require_ready must be a boolean")

        normalized_item: dict[str, Any] = {
            "id": item_id,
            "url": url,
            "enabled": item.get("enabled", True),
            "session": {
                "storage_state_path": item.get("storage_state_path") or session.get("storage_state_path"),
                "storage_state_env": item.get("storage_state_env") or session.get("storage_state_env"),
                "user_data_dir": item.get("user_data_dir") or session.get("user_data_dir"),
                "user_data_dir_env": item.get("user_data_dir_env") or session.get("user_data_dir_env"),
                "storage_state_output_path": item.get("storage_state_output_path") or session.get("storage_state_output_path"),
            },
            "expected": {"selectors": selectors, "scores": item_scores},
            "breakpoints": ensure_string_list(item.get("breakpoints"), f"{label}.breakpoints", errors),
            "source_signals": ensure_string_list(item.get("source_signals"), f"{label}.source_signals", errors),
            "license_text": ensure_string(item.get("license_text"), f"{label}.license_text", errors),
        }
        for int_key in ("wait_seconds", "timeout_seconds", "viewport_width", "viewport_height"):
            value = ensure_int(item.get(int_key), f"{label}.{int_key}", errors)
            if value is not None:
                normalized_item[int_key] = value
        viewport = item.get("viewport")
        if viewport is not None:
            viewport_dict = ensure_dict(viewport, f"{label}.viewport", errors)
            width = ensure_int(viewport_dict.get("width"), f"{label}.viewport.width", errors)
            height = ensure_int(viewport_dict.get("height"), f"{label}.viewport.height", errors)
            if width is not None:
                normalized_item["viewport_width"] = width
            if height is not None:
                normalized_item["viewport_height"] = height
        for bool_key in ("skip_runtime_trace", "skip_html", "skip_screenshot", "not_exact"):
            if item.get(bool_key) is not None:
                if isinstance(item.get(bool_key), bool):
                    normalized_item[bool_key] = item[bool_key]
                else:
                    errors.append(f"{label}.{bool_key} must be a boolean")
        if not isinstance(normalized_item["enabled"], bool):
            errors.append(f"{label}.enabled must be a boolean")
            normalized_item["enabled"] = True
        normalized["items"].append(normalized_item)
    return normalized, errors


def resolve_env_reference(env_name: Any, label: str) -> tuple[str | None, str | None]:
    if env_name is None:
        return None, None
    if not isinstance(env_name, str) or not env_name.strip():
        return None, f"{label} must be a non-empty environment variable name"
    value = os.environ.get(env_name.strip())
    if not value:
        return None, f"environment variable {env_name.strip()} is not set"
    return value, None


def expand_env_path(raw_path: Any, label: str) -> tuple[str | None, str | None]:
    if raw_path is None:
        return None, None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, f"{label} must be a non-empty string"
    missing = [
        match.group("braced") or match.group("plain")
        for match in ENV_PATTERN.finditer(raw_path)
        if os.environ.get(match.group("braced") or match.group("plain")) is None
    ]
    if missing:
        return None, f"environment variable {missing[0]} is not set"
    return os.path.expandvars(os.path.expanduser(raw_path.strip())), None


def resolve_session(session: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    resolved: dict[str, str] = {}
    storage_from_env, storage_env_error = resolve_env_reference(session.get("storage_state_env"), "storage_state_env")
    storage_raw = storage_from_env or session.get("storage_state_path")
    storage_path, storage_path_error = expand_env_path(storage_raw, "storage_state_path")

    user_data_from_env, user_data_env_error = resolve_env_reference(session.get("user_data_dir_env"), "user_data_dir_env")
    user_data_raw = user_data_from_env or session.get("user_data_dir")
    user_data_dir, user_data_error = expand_env_path(user_data_raw, "user_data_dir")

    output_path, output_error = expand_env_path(session.get("storage_state_output_path"), "storage_state_output_path")
    errors = [error for error in (storage_env_error, storage_path_error, user_data_env_error, user_data_error, output_error) if error]
    if errors:
        return resolved, errors[0]

    if storage_path:
        storage = Path(storage_path).expanduser().resolve()
        if not storage.is_file():
            return resolved, f"storage_state_path does not exist or is not a file: {storage}"
        resolved["storage_state_path"] = str(storage)
    if user_data_dir:
        profile = Path(user_data_dir).expanduser().resolve()
        if not profile.is_dir():
            return resolved, f"user_data_dir does not exist or is not a directory: {profile}"
        resolved["user_data_dir"] = str(profile)
    if output_path:
        resolved["storage_state_output_path"] = str(Path(output_path).expanduser().resolve())
    if not resolved.get("storage_state_path") and not resolved.get("user_data_dir"):
        return resolved, "authenticated corpus item requires storage_state_path or user_data_dir"
    return resolved, None


def redact_command(command: list[str]) -> str:
    redacted: list[str] = []
    redact_next = False
    for token in command:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        redacted.append(token)
        if token in SECRET_ARG_FLAGS:
            redact_next = True
    return shlex.join(redacted)


def build_clone_command(item: dict[str, Any], output_dir: Path, session: dict[str, str], defaults: dict[str, Any]) -> list[str]:
    wait_seconds = item.get("wait_seconds", defaults.get("wait_seconds", 8))
    timeout_seconds = item.get("timeout_seconds", defaults.get("timeout_seconds", 35))
    viewport_width = item.get("viewport_width", defaults.get("viewport_width", 1440))
    viewport_height = item.get("viewport_height", defaults.get("viewport_height", 1200))
    breakpoints = item.get("breakpoints") or defaults.get("breakpoints") or []
    source_signals = item.get("source_signals") or defaults.get("source_signals") or []
    command = [
        "node",
        "./bin/web-embedding.mjs",
        "clone",
        "--url",
        item["url"],
        "--output-dir",
        str(output_dir),
        "--wait-seconds",
        str(wait_seconds),
        "--timeout-seconds",
        str(timeout_seconds),
        "--viewport-width",
        str(viewport_width),
        "--viewport-height",
        str(viewport_height),
    ]
    if session.get("storage_state_path"):
        command.extend(["--storage-state-path", session["storage_state_path"]])
    if session.get("user_data_dir"):
        command.extend(["--user-data-dir", session["user_data_dir"]])
    if session.get("storage_state_output_path"):
        command.extend(["--storage-state-output-path", session["storage_state_output_path"]])
    if breakpoints:
        command.extend(["--breakpoints", *breakpoints])
    if item.get("license_text"):
        command.extend(["--license-text", item["license_text"]])
    if source_signals:
        command.extend(["--source-signals", *source_signals])
    if item.get("skip_runtime_trace", defaults.get("skip_runtime_trace")):
        command.append("--skip-runtime-trace")
    if item.get("skip_html", defaults.get("skip_html")):
        command.append("--skip-html")
    if item.get("skip_screenshot", defaults.get("skip_screenshot")):
        command.append("--skip-screenshot")
    if item.get("not_exact", defaults.get("not_exact")):
        command.append("--not-exact")
    return command


def load_output_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_summary(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "reproduction" / "self-verify" / "summary.json"
    if not path.is_file():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def load_pipeline_manifest(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "pipeline-run-manifest.json"
    if not path.is_file():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def pipeline_needs_session_reason(clone_payload: dict[str, Any] | None, output_dir: Path) -> str | None:
    manifest: dict[str, Any] | None = None
    if isinstance(clone_payload, dict) and isinstance(clone_payload.get("pipeline_run_manifest"), dict):
        manifest = clone_payload["pipeline_run_manifest"]
    if manifest is None:
        manifest = load_pipeline_manifest(output_dir)
    if not isinstance(manifest, dict):
        return None
    classification = manifest.get("failure_classification")
    if not isinstance(classification, dict) or classification.get("status") != "needs-session":
        return None
    codes = classification.get("codes")
    code_text = ", ".join(str(code) for code in codes) if isinstance(codes, list) else ""
    return f"pipeline classified item as needs-session{': ' + code_text if code_text else ''}"


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def summary_score(summary: dict[str, Any]) -> float | None:
    comparison = summary.get("comparison_summary")
    if isinstance(comparison, dict):
        score = numeric(comparison.get("score"))
        if score is not None:
            return score
    return numeric(summary.get("score"))


def summary_ready(summary: dict[str, Any]) -> bool | None:
    for key in ("overall_ready_for_exact_clone", "exact_ready", "ready"):
        if isinstance(summary.get(key), bool):
            return bool(summary[key])
    root_report = summary.get("root_report")
    if isinstance(root_report, dict) and isinstance(root_report.get("ready_for_exact_clone"), bool):
        return bool(root_report["ready_for_exact_clone"])
    return None


def effective_scores(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    scores = dict(defaults.get("scores") or {})
    scores.update(item.get("expected", {}).get("scores") or {})
    return scores


def check_scores(summary: dict[str, Any] | None, scores: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    result = {
        "expected": scores,
        "actual": {},
        "passed": True,
    }
    if not scores:
        return result, []
    failures: list[str] = []
    if not isinstance(summary, dict):
        result["passed"] = False
        return result, ["missing reproduction/self-verify/summary.json"]
    actual_score = summary_score(summary)
    actual_screen = numeric(summary.get("screen_clone_score"))
    actual_breakpoint = numeric(summary.get("breakpoint_score_average"))
    actual_ready = summary_ready(summary)
    result["actual"] = {
        "score": actual_score,
        "screen_clone_score": actual_screen,
        "breakpoint_score_average": actual_breakpoint,
        "ready": actual_ready,
    }
    comparisons = [
        ("score", actual_score, scores.get("min_score")),
        ("screen_clone_score", actual_screen, scores.get("min_screen_score")),
        ("breakpoint_score_average", actual_breakpoint, scores.get("min_breakpoint_average")),
    ]
    for name, actual, minimum in comparisons:
        if minimum is None:
            continue
        if actual is None or actual < float(minimum):
            failures.append(f"{name} {actual} < {minimum}")
    if scores.get("require_ready") is True and actual_ready is not True:
        failures.append(f"ready {actual_ready} != True")
    result["passed"] = not failures
    return result, failures


def find_chrome_path() -> str | None:
    env_path = os.environ.get("WEB_EMBEDDING_CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(executable)
        if found:
            return found
    return None


SELECTOR_CHECK_JS = r"""
const fs = require("node:fs");
const { pathToFileURL } = require("node:url");

(async () => {
  const payload = JSON.parse(fs.readFileSync(0, "utf8"));
  const { chromium } = require("playwright-core");
  const launchOptions = { headless: true };
  if (payload.executablePath) {
    launchOptions.executablePath = payload.executablePath;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  await page.goto(pathToFileURL(payload.htmlPath).href, { waitUntil: "domcontentloaded" });
  const checks = [];
  for (const expected of payload.selectors) {
    const check = {
      selector: expected.selector,
      min_count: expected.min_count ?? 1,
      max_count: expected.max_count,
      text_contains: expected.text_contains,
      count: 0,
      passed: false
    };
    try {
      const locator = page.locator(expected.selector);
      check.count = await locator.count();
      check.passed = check.count >= check.min_count;
      if (expected.max_count !== undefined && check.count > expected.max_count) {
        check.passed = false;
      }
      if (expected.text_contains) {
        const textValues = await locator.allTextContents();
        check.text_matched = textValues.some((value) => value.includes(expected.text_contains));
        check.passed = check.passed && check.text_matched;
      }
    } catch (error) {
      check.error = error.message;
      check.passed = false;
    }
    checks.push(check);
  }
  await browser.close();
  process.stdout.write(JSON.stringify({ available: true, checks }));
})().catch((error) => {
  process.stdout.write(JSON.stringify({ available: false, error: error.message }));
  process.exitCode = 1;
});
"""


def check_selectors(output_dir: Path, selectors: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {
        "expected": selectors,
        "passed": True,
        "checks": [],
    }
    if not selectors:
        return result, []
    html_path = output_dir / "dom" / "runtime.html"
    if not html_path.is_file():
        result["passed"] = False
        return result, [f"missing runtime HTML for selector checks: {html_path}"]
    chrome_path = find_chrome_path()
    payload = {"htmlPath": str(html_path), "selectors": selectors, "executablePath": chrome_path}
    completed = subprocess.run(
        ["node", "-e", SELECTOR_CHECK_JS],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    selector_payload = load_output_json(completed.stdout.strip())
    if not isinstance(selector_payload, dict):
        result["passed"] = False
        details = completed.stderr.strip() or completed.stdout.strip() or "selector check produced no JSON output"
        return result, [details]
    result.update(selector_payload)
    failures: list[str] = []
    for check in selector_payload.get("checks", []):
        if not isinstance(check, dict) or check.get("passed") is True:
            continue
        reason = f"{check.get('selector')}: count {check.get('count')} < {check.get('min_count')}"
        if check.get("max_count") is not None and isinstance(check.get("count"), int) and check["count"] > check["max_count"]:
            reason = f"{check.get('selector')}: count {check.get('count')} > {check.get('max_count')}"
        if check.get("text_contains") and check.get("text_matched") is not True:
            reason = f"{check.get('selector')}: text did not include {check.get('text_contains')!r}"
        if check.get("error"):
            reason = f"{check.get('selector')}: {check.get('error')}"
        failures.append(reason)
    if selector_payload.get("available") is not True and selector_payload.get("error"):
        failures.append(str(selector_payload["error"]))
    result["passed"] = not failures
    return result, failures


def run_clone(command: list[str], output_dir: Path) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
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


def item_output_dir(output_root: Path, item: dict[str, Any], index: int) -> Path:
    return output_root / f"{index:02d}-{slugify(str(item.get('id') or item['url']))}"


def build_skipped_item(item: dict[str, Any], output_dir: Path, status: str, reason: str, command: list[str] | None = None, *, runnable: bool = False) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "url": item.get("url"),
        "output_dir": str(output_dir),
        "status": status,
        "runnable": runnable,
        "command": redact_command(command or []),
        "reason": reason,
    }


def run_item(
    item: dict[str, Any],
    defaults: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool,
    validate_only: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if item.get("enabled") is False:
        result = build_skipped_item(item, output_dir, "skipped", "item disabled in manifest")
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result
    if validate_only:
        result = build_skipped_item(item, output_dir, "skipped", "validate-only requested; schema validated without resolving session paths")
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result

    session, session_error = resolve_session(item.get("session", {}))
    command = build_clone_command(item, output_dir, session, defaults)
    if session_error:
        result = build_skipped_item(item, output_dir, "needs_session", session_error, command)
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result
    if dry_run:
        result = build_skipped_item(item, output_dir, "skipped", "dry-run requested; clone/check not executed", command, runnable=True)
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result

    started = time.monotonic()
    completed = run_clone(command, output_dir)
    duration = round(time.monotonic() - started, 3)
    stdout_path = output_dir / "clone-stdout.json"
    stderr_path = output_dir / "clone-stderr.log"
    stdout_path.write_text(completed.stdout)
    stderr_path.write_text(completed.stderr)

    result: dict[str, Any] = {
        "id": item.get("id"),
        "url": item.get("url"),
        "output_dir": str(output_dir),
        "status": "succeeded",
        "runnable": True,
        "command": redact_command(command),
        "reason": "clone and checks passed",
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    if completed.returncode != 0:
        reason = f"clone failed with exit code {completed.returncode}"
        if completed.stderr.strip():
            reason = f"{reason}: {completed.stderr.strip().splitlines()[-1][:240]}"
        result["status"] = "failed"
        result["reason"] = reason
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result

    clone_payload = load_output_json(completed.stdout)
    if isinstance(clone_payload, dict):
        result["clone_summary"] = {
            "policy_mode": clone_payload.get("policy_mode"),
            "coverage": clone_payload.get("coverage"),
            "next_action": clone_payload.get("next_action"),
            "exact_ready": clone_payload.get("exact_ready"),
            "pipeline_run_manifest": ((clone_payload.get("pipeline_run_manifest") or {}).get("path") if isinstance(clone_payload.get("pipeline_run_manifest"), dict) else None),
        }
    needs_session_reason = pipeline_needs_session_reason(clone_payload, output_dir)
    if needs_session_reason:
        result["status"] = "needs_session"
        result["reason"] = needs_session_reason
        write_json(output_dir / "authenticated-corpus-item.json", result)
        return result

    summary = load_summary(output_dir)
    scores, score_failures = check_scores(summary, effective_scores(item, defaults))
    selectors, selector_failures = check_selectors(output_dir, item.get("expected", {}).get("selectors") or [])
    result["checks"] = {
        "scores": scores,
        "selectors": selectors,
    }
    failures = score_failures + selector_failures
    if failures:
        result["status"] = "failed"
        result["reason"] = "; ".join(failures[:5])
    write_json(output_dir / "authenticated-corpus-item.json", result)
    return result


def summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(items),
        "runnable": sum(1 for item in items if item.get("runnable") is True),
        "skipped": sum(1 for item in items if item.get("status") == "skipped"),
        "succeeded": sum(1 for item in items if item.get("status") == "succeeded"),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
        "needs_session": sum(1 for item in items if item.get("status") == "needs_session"),
    }
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Authenticated dashboard corpus manifest JSON.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_ROOT), help="Output root for per-item artifacts.")
    parser.add_argument("--report", help=f"Report JSON path. Defaults to <out>/{DEFAULT_REPORT_NAME}.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve sessions and commands but do not run clone/check.")
    parser.add_argument("--validate-only", action="store_true", help="Validate manifest schema only; do not resolve sessions or run clone/check.")
    parser.add_argument("--fail-on-needs-session", action="store_true", help="Exit non-zero when any item needs a missing session.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest_path = Path(args.manifest).expanduser().resolve()
    payload = load_json(manifest_path)
    manifest, errors = validate_manifest(payload)
    output_root = Path(args.out).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve() if args.report else output_root / DEFAULT_REPORT_NAME
    output_root.mkdir(parents=True, exist_ok=True)

    if errors:
        report = {
            "schema_version": 1,
            "run": {
                "timestamp": utc_now(),
                "manifest_path": str(manifest_path),
                "output_root": str(output_root),
                "dry_run": bool(args.dry_run),
                "validate_only": bool(args.validate_only),
            },
            "total": 0,
            "runnable": 0,
            "skipped": 0,
            "succeeded": 0,
            "failed": 0,
            "needs_session": 0,
            "summary": {
                "total": 0,
                "runnable": 0,
                "skipped": 0,
                "succeeded": 0,
                "failed": 0,
                "needs_session": 0,
            },
            "items": [],
            "errors": errors,
        }
        write_json(report_path, report)
        print(f"Manifest validation failed; report written to {report_path}", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    items: list[dict[str, Any]] = []
    for index, item in enumerate(manifest["items"], start=1):
        result = run_item(
            item,
            manifest.get("defaults", {}),
            item_output_dir(output_root, item, index),
            dry_run=bool(args.dry_run),
            validate_only=bool(args.validate_only),
        )
        items.append(result)

    summary = summarize(items)
    report = {
        "schema_version": 1,
        "run": {
            "timestamp": utc_now(),
            "tool": "authenticated dashboard live corpus runner",
            "manifest_path": str(manifest_path),
            "corpus_name": manifest.get("corpus_name"),
            "output_root": str(output_root),
            "dry_run": bool(args.dry_run),
            "validate_only": bool(args.validate_only),
        },
        **summary,
        "summary": summary,
        "items": items,
    }
    write_json(report_path, report)
    print(f"Authenticated corpus report written to {report_path}")
    print(
        "summary: "
        f"total={summary['total']} runnable={summary['runnable']} skipped={summary['skipped']} "
        f"succeeded={summary['succeeded']} failed={summary['failed']} needs_session={summary['needs_session']}"
    )

    if summary["failed"]:
        return 1
    if args.fail_on_needs_session and summary["needs_session"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
