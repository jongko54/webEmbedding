#!/usr/bin/env python3
"""Smoke checks for deterministic HAR replay matching."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_har_replay() -> Any:
    mcp_root = repo_root() / "bundle" / "source-first-clone" / "mcp"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from source_first_clone.har_replay import HarReplayEngine, build_replay_report, request_matcher

    return HarReplayEngine, build_replay_report, request_matcher


def fixture_har() -> dict[str, Any]:
    encoded_body = base64.b64encode(b"created").decode("ascii")
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "webEmbedding smoke", "version": "1"},
            "pages": [{"id": "page_1", "startedDateTime": "2026-05-06T00:00:00Z", "title": "Smoke"}],
            "entries": [
                {
                    "startedDateTime": "2026-05-06T00:00:01Z",
                    "request": {
                        "method": "GET",
                        "url": "https://fixture.example/api/items?b=2&a=1",
                        "headers": [{"name": "accept", "value": "application/json"}],
                        "queryString": [{"name": "b", "value": "2"}, {"name": "a", "value": "1"}],
                    },
                    "response": {
                        "status": 200,
                        "statusText": "OK",
                        "headers": [
                            {"name": "content-type", "value": "application/json"},
                            {"name": "transfer-encoding", "value": "chunked"},
                        ],
                        "content": {"mimeType": "application/json", "text": "{\"ok\":true}"},
                    },
                },
                {
                    "startedDateTime": "2026-05-06T00:00:02Z",
                    "request": {
                        "method": "POST",
                        "url": "https://fixture.example/api/search",
                        "headers": [{"name": "content-type", "value": "application/json"}],
                        "postData": {"mimeType": "application/json", "text": "{\"q\":\"abc\"}"},
                    },
                    "response": {
                        "status": 201,
                        "statusText": "Created",
                        "headers": [{"name": "content-type", "value": "text/plain"}],
                        "content": {"mimeType": "text/plain", "text": encoded_body, "encoding": "base64"},
                    },
                },
            ],
        }
    }


def assert_replay() -> None:
    HarReplayEngine, build_replay_report, request_matcher = load_har_replay()
    with TemporaryDirectory(prefix="har-replay-smoke-") as temp_name:
        root = Path(temp_name)
        har_path = root / "network" / "har.json"
        report_path = root / "network" / "replay-report.json"
        har_path.parent.mkdir(parents=True)
        har_path.write_text(json.dumps(fixture_har(), indent=2) + "\n")

        engine = HarReplayEngine.from_file(har_path)
        summary = engine.summary()
        if summary["entry_count"] != 2 or summary["entries_with_response_body"] != 2:
            raise AssertionError(f"unexpected replay summary: {summary}")

        get_result = engine.replay_request("GET", "https://fixture.example/api/items?a=1&b=2")
        if get_result.get("matched") is not True:
            raise AssertionError(f"GET query-order replay did not match: {get_result}")
        response = get_result.get("response") or {}
        if response.get("status") != 200 or response.get("headers", {}).get("transfer-encoding"):
            raise AssertionError(f"GET replay response was not sanitized: {response}")
        if response.get("body") != "{\"ok\":true}":
            raise AssertionError(f"GET replay body mismatch: {response}")

        post_result = engine.replay_request("POST", "https://fixture.example/api/search", "{\"q\":\"abc\"}")
        post_response = post_result.get("response") or {}
        if post_result.get("matched") is not True or post_response.get("status") != 201:
            raise AssertionError(f"POST replay did not match: {post_result}")
        if post_response.get("body_encoding") != "base64" or post_response.get("body") != base64.b64encode(b"created").decode("ascii"):
            raise AssertionError(f"POST base64 replay body mismatch: {post_response}")

        missing_result = engine.replay_request("DELETE", "https://fixture.example/api/search")
        if missing_result.get("matched") is not False or missing_result.get("match_type") != "missing":
            raise AssertionError(f"missing replay request was not reported clearly: {missing_result}")

        body_mismatch_result = engine.replay_request("POST", "https://fixture.example/api/search")
        if body_mismatch_result.get("matched") is not False or body_mismatch_result.get("match_type") != "body_mismatch":
            raise AssertionError(f"request body mismatch was not reported clearly: {body_mismatch_result}")

        matcher = request_matcher(har_path)
        matcher_mismatch = matcher.match("POST", "https://fixture.example/api/search", post_data="{\"q\":\"different\"}")
        if matcher_mismatch.get("matched") is not False:
            raise AssertionError(f"mapping matcher should not replay a different request body: {matcher_mismatch}")

        report = build_replay_report(
            har_path,
            [
                {"id": "items", "method": "GET", "url": "https://fixture.example/api/items?a=1&b=2"},
                {"id": "search", "method": "POST", "url": "https://fixture.example/api/search", "postData": "{\"q\":\"abc\"}"},
            ],
            output_path=report_path,
        )
        if report.get("summary", {}).get("ready") is not True:
            raise AssertionError(f"replay report was not ready: {report}")
        if not report_path.is_file():
            raise AssertionError("replay report was not persisted")


def main() -> int:
    assert_replay()
    print("HAR replay smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
