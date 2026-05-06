#!/usr/bin/env python3
"""Smoke checks for the filesystem clone job queue.

The runners below are deterministic fakes. They write the same
pipeline-run-manifest shape as clone_reference_url, but never touch the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_job_queue() -> Any:
    mcp_root = repo_root() / "bundle" / "source-first-clone" / "mcp"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from source_first_clone.job_queue import JobQueue

    return JobQueue


def fake_runner(status: str, codes: list[str] | None = None) -> Any:
    def run(**kwargs: Any) -> dict[str, Any]:
        output_dir = Path(str(kwargs["output_dir"])).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "run_id": output_dir.name,
            "input": {"url": kwargs["url"]},
            "failure_classification": {
                "status": status,
                "codes": list(codes or []),
                "issues": [{"code": code, "severity": status, "action": "smoke", "evidence": "smoke"} for code in (codes or [])],
            },
            "artifacts": {"capture": {}, "reproduction": {}},
        }
        manifest_path = output_dir / "pipeline-run-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest["path"] = str(manifest_path)
        return {
            "url": kwargs["url"],
            "next_action": "smoke",
            "pipeline_run_manifest": manifest,
        }

    return run


def assert_state(job: dict[str, Any], expected: str) -> None:
    actual = job.get("state")
    if actual != expected:
        raise AssertionError(f"expected state {expected!r}, got {actual!r}: {job}")


def assert_success(JobQueue: Any, root: Path) -> None:
    queue = JobQueue(root / "success", retry_delay_seconds=0)
    job = queue.enqueue("https://fixture.example/success")
    result = queue.run_next(runner=fake_runner("ready"), worker_id="smoke-success")
    if result is None:
        raise AssertionError("run_next did not claim queued job")
    assert_state(result, "succeeded")
    if result.get("id") != job.get("id"):
        raise AssertionError("run_next returned the wrong job")
    if result.get("attempts") != 1:
        raise AssertionError(f"success attempts should be 1: {result.get('attempts')}")
    manifest = result.get("pipeline_run_manifest")
    if not isinstance(manifest, dict) or (manifest.get("job") or {}).get("id") != job.get("id"):
        raise AssertionError("pipeline manifest was not annotated with queue job metadata")
    loaded = queue.load(job["id"])
    assert_state(loaded, "succeeded")


def assert_retry_wait(JobQueue: Any, root: Path) -> None:
    queue = JobQueue(root / "retry", retry_delay_seconds=60)
    job = queue.enqueue("https://fixture.example/retry", max_attempts=2)
    result = queue.run_job(job["id"], runner=fake_runner("manual-review", ["network-replay-limited"]), worker_id="smoke-retry")
    assert_state(result, "retry_wait")
    if result.get("attempts") != 1:
        raise AssertionError(f"retry attempts should be 1: {result.get('attempts')}")
    if not result.get("next_retry_at"):
        raise AssertionError("retry_wait job is missing next_retry_at")
    if queue.run_next(runner=fake_runner("ready")) is not None:
        raise AssertionError("run_next should not claim retry_wait before next_retry_at")


def assert_blocked_and_needs_session(JobQueue: Any, root: Path) -> None:
    queue = JobQueue(root / "classified", retry_delay_seconds=0)
    blocked = queue.enqueue("https://fixture.example/blocked")
    blocked_result = queue.run_job(blocked["id"], runner=fake_runner("blocked", ["blocked-by-policy"]), worker_id="smoke-blocked")
    assert_state(blocked_result, "blocked")
    if blocked_result.get("next_retry_at") is not None:
        raise AssertionError("blocked jobs must not schedule retry")

    needs_session = queue.enqueue("https://fixture.example/session")
    session_result = queue.run_job(
        needs_session["id"],
        runner=fake_runner("needs-session", ["auth-session-missing"]),
        worker_id="smoke-session",
    )
    assert_state(session_result, "needs_session")
    if session_result.get("next_retry_at") is not None:
        raise AssertionError("needs_session jobs must not schedule retry")


def assert_list_load_cancel(JobQueue: Any, root: Path) -> None:
    queue = JobQueue(root / "ops", retry_delay_seconds=0)
    first = queue.enqueue("https://fixture.example/first")
    second = queue.enqueue("https://fixture.example/second")
    listed = queue.list()
    if [job["id"] for job in listed] != [first["id"], second["id"]]:
        raise AssertionError(f"jobs should list in creation order: {listed}")
    loaded = queue.load(first["id"])
    if loaded.get("url") != "https://fixture.example/first":
        raise AssertionError(f"load returned the wrong job: {loaded}")
    cancelled = queue.cancel(second["id"], reason="smoke")
    assert_state(cancelled, "cancelled")
    if queue.run_job(second["id"], runner=fake_runner("ready")).get("state") != "cancelled":
        raise AssertionError("cancelled terminal jobs must not be run")


def main() -> int:
    JobQueue = load_job_queue()
    with TemporaryDirectory() as temp_name:
        root = Path(temp_name)
        assert_success(JobQueue, root)
        assert_retry_wait(JobQueue, root)
        assert_blocked_and_needs_session(JobQueue, root)
        assert_list_load_cancel(JobQueue, root)
    print("Job queue smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
