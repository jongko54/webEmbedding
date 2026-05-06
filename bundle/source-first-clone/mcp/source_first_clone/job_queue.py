"""Filesystem-backed asynchronous clone job queue.

The worker step is intentionally synchronous: a worker claims one JSON job,
calls the clone runner, and persists the updated job record. The asynchronous
part is the durable job record and retry schedule, which lets callers enqueue
work and have any later worker pick up due jobs from the filesystem.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .orchestration import clone_reference_url


CloneRunner = Callable[..., dict[str, Any]]

SCHEMA_VERSION = 1
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 30
DEFAULT_RETRY_MULTIPLIER = 2.0
DEFAULT_RETRYABLE_CODES = {
    "network-replay-limited",
    "network-request-failures",
}

TERMINAL_STATES = {
    "succeeded",
    "failed",
    "blocked",
    "needs_session",
    "manual_review",
    "cancelled",
}
RUNNABLE_STATES = {"queued", "retry_wait"}
VALID_STATES = RUNNABLE_STATES | TERMINAL_STATES | {"running"}


class JobQueueError(RuntimeError):
    """Base error raised by the job queue."""


class JobNotFoundError(JobQueueError):
    """Raised when a job id has no JSON record under the queue root."""


class JobLockedError(JobQueueError):
    """Raised when another worker already owns the job lock."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _ensure_queue_root(queue_root: str | Path) -> Path:
    root = Path(queue_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".tmp").mkdir(exist_ok=True)
    (root / ".locks").mkdir(exist_ok=True)
    return root


def _job_path(queue_root: Path, job_id: str) -> Path:
    if not job_id or "/" in job_id or "\\" in job_id:
        raise ValueError(f"invalid job id: {job_id!r}")
    return queue_root / f"{job_id}.json"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise JobQueueError(f"{path} must contain a JSON object")
    return payload


def _write_job(queue_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job.get("id") or "")
    path = _job_path(queue_root, job_id)
    tmp_path = queue_root / ".tmp" / f"{job_id}.{uuid.uuid4().hex}.tmp"
    tmp_path.write_text(json.dumps(job, indent=2, sort_keys=True, default=_json_default) + "\n")
    os.replace(tmp_path, path)
    return job


def _load_job(queue_root: Path, job_id: str) -> dict[str, Any]:
    path = _job_path(queue_root, job_id)
    if not path.is_file():
        raise JobNotFoundError(f"job not found: {job_id}")
    return _read_json(path)


def _job_sort_key(job: dict[str, Any]) -> tuple[str, str]:
    state = str(job.get("state") or "")
    if state == "retry_wait":
        primary = str(job.get("next_retry_at") or job.get("updated_at") or "")
    else:
        primary = str(job.get("created_at") or "")
    return primary, str(job.get("id") or "")


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(job, default=_json_default))


def _normalize_state(value: Any) -> str:
    state = str(value or "").strip().lower().replace("-", "_")
    return state if state in VALID_STATES else "succeeded"


def _classification_from_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    classification = manifest.get("failure_classification")
    return classification if isinstance(classification, dict) else {}


def _pipeline_manifest_from_result(output_dir: str | None, result: dict[str, Any]) -> dict[str, Any] | None:
    manifest = result.get("pipeline_run_manifest")
    if isinstance(manifest, dict):
        return manifest

    if not output_dir:
        return None
    manifest_path = Path(output_dir).expanduser().resolve() / "pipeline-run-manifest.json"
    if not manifest_path.is_file():
        return None
    payload = _read_json(manifest_path)
    payload.setdefault("path", str(manifest_path))
    return payload


def _manifest_path(output_dir: str | None, manifest: dict[str, Any] | None) -> Path | None:
    if isinstance(manifest, dict) and manifest.get("path"):
        return Path(str(manifest["path"])).expanduser().resolve()
    if output_dir:
        return Path(output_dir).expanduser().resolve() / "pipeline-run-manifest.json"
    return None


def _write_manifest_job_metadata(
    *,
    job: dict[str, Any],
    manifest: dict[str, Any] | None,
    started_at: str,
    finished_at: str,
    worker_id: str,
) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    updated = json.loads(json.dumps(manifest, default=_json_default))
    updated["job"] = {
        "id": job.get("id"),
        "state": job.get("state"),
        "attempt": job.get("attempts"),
        "max_attempts": job.get("max_attempts"),
        "worker_id": worker_id,
        "queued_at": job.get("created_at"),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    path = _manifest_path(job.get("output_dir"), updated)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2, sort_keys=True, default=_json_default) + "\n")
        updated["path"] = str(path)
    return updated


def _classification_codes(classification: dict[str, Any]) -> set[str]:
    codes = classification.get("codes")
    if isinstance(codes, list):
        return {str(code) for code in codes}
    issues = classification.get("issues")
    if isinstance(issues, list):
        return {str(issue.get("code")) for issue in issues if isinstance(issue, dict) and issue.get("code")}
    return set()


def _retryable_codes(job: dict[str, Any]) -> set[str]:
    policy = job.get("retry_policy")
    if not isinstance(policy, dict):
        return set(DEFAULT_RETRYABLE_CODES)
    raw_codes = policy.get("retryable_codes")
    if not isinstance(raw_codes, list):
        return set(DEFAULT_RETRYABLE_CODES)
    return {str(code) for code in raw_codes}


def _max_attempts(job: dict[str, Any]) -> int:
    try:
        return max(1, int(job.get("max_attempts") or DEFAULT_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS


def _retry_delay(job: dict[str, Any]) -> int:
    policy = job.get("retry_policy")
    if not isinstance(policy, dict):
        return DEFAULT_RETRY_DELAY_SECONDS
    try:
        base_value = policy.get("base_delay_seconds")
        base = int(DEFAULT_RETRY_DELAY_SECONDS if base_value is None else base_value)
    except (TypeError, ValueError):
        base = DEFAULT_RETRY_DELAY_SECONDS
    try:
        multiplier_value = policy.get("multiplier")
        multiplier = float(DEFAULT_RETRY_MULTIPLIER if multiplier_value is None else multiplier_value)
    except (TypeError, ValueError):
        multiplier = DEFAULT_RETRY_MULTIPLIER
    attempt = max(1, int(job.get("attempts") or 1))
    return max(0, int(base * (multiplier ** (attempt - 1))))


def _next_retry_at(job: dict[str, Any]) -> str:
    return (utc_now() + timedelta(seconds=_retry_delay(job))).isoformat()


def _should_retry_classification(job: dict[str, Any], classification: dict[str, Any]) -> bool:
    if int(job.get("attempts") or 0) >= _max_attempts(job):
        return False
    return bool(_classification_codes(classification) & _retryable_codes(job))


def _state_from_classification(job: dict[str, Any], classification: dict[str, Any]) -> str:
    raw_status = str(classification.get("status") if classification else "ready").strip().lower()
    if not raw_status or raw_status == "ready":
        return "succeeded"
    status = raw_status.replace("-", "_")
    if status in {"blocked", "needs_session"}:
        return status
    if _should_retry_classification(job, classification):
        return "retry_wait"
    if status == "manual_review":
        return "manual_review"
    return status if status in VALID_STATES else "succeeded"


def _is_due(job: dict[str, Any], now: datetime | None = None) -> bool:
    state = str(job.get("state") or "")
    if state == "queued":
        return True
    if state != "retry_wait":
        return False
    retry_at = parse_timestamp(job.get("next_retry_at"))
    return retry_at is None or retry_at <= (now or utc_now())


@contextmanager
def _job_lock(queue_root: Path, job_id: str, worker_id: str, stale_after_seconds: int = 3600) -> Iterator[None]:
    lock_path = queue_root / ".locks" / f"{job_id}.lock"
    lock_payload = {
        "job_id": job_id,
        "worker_id": worker_id,
        "created_at": isoformat(),
        "pid": os.getpid(),
    }
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            try:
                existing = _read_json(lock_path)
            except (OSError, json.JSONDecodeError, JobQueueError):
                existing = {}
            created_at = parse_timestamp(existing.get("created_at"))
            if created_at and (utc_now() - created_at).total_seconds() > stale_after_seconds:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise JobLockedError(f"job is locked by another worker: {job_id}") from exc
        with os.fdopen(fd, "w") as handle:
            json.dump(lock_payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        break

    try:
        yield
    finally:
        try:
            current = _read_json(lock_path)
        except (OSError, json.JSONDecodeError, JobQueueError):
            current = {}
        if current.get("worker_id") == worker_id:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


class JobQueue:
    """Durable JSON queue for source-first clone jobs."""

    def __init__(
        self,
        queue_root: str | Path,
        *,
        runner: CloneRunner | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
        retry_multiplier: float = DEFAULT_RETRY_MULTIPLIER,
        retryable_codes: list[str] | None = None,
    ) -> None:
        self.queue_root = _ensure_queue_root(queue_root)
        self.runner = runner
        self.default_max_attempts = max(1, int(max_attempts))
        self.default_retry_delay_seconds = max(0, int(retry_delay_seconds))
        self.default_retry_multiplier = float(retry_multiplier)
        self.default_retryable_codes = list(retryable_codes or sorted(DEFAULT_RETRYABLE_CODES))

    def enqueue(
        self,
        url: str,
        *,
        output_dir: str | Path | None = None,
        job_id: str | None = None,
        clone_args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        max_attempts: int | None = None,
        retry_delay_seconds: int | None = None,
        retry_multiplier: float | None = None,
        retryable_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        if not url:
            raise ValueError("url is required")
        resolved_job_id = job_id or uuid.uuid4().hex
        path = _job_path(self.queue_root, resolved_job_id)
        if path.exists():
            raise JobQueueError(f"job already exists: {resolved_job_id}")

        job_output_dir = Path(output_dir).expanduser().resolve() if output_dir else self.queue_root / "outputs" / resolved_job_id
        created_at = isoformat()
        attempts = max(1, int(max_attempts)) if max_attempts is not None else self.default_max_attempts
        delay = max(0, int(retry_delay_seconds)) if retry_delay_seconds is not None else self.default_retry_delay_seconds
        multiplier = float(retry_multiplier) if retry_multiplier is not None else self.default_retry_multiplier
        job = {
            "schema_version": SCHEMA_VERSION,
            "id": resolved_job_id,
            "url": url,
            "state": "queued",
            "created_at": created_at,
            "updated_at": created_at,
            "attempts": 0,
            "max_attempts": attempts,
            "next_retry_at": None,
            "output_dir": str(job_output_dir),
            "clone_args": dict(clone_args or {}),
            "metadata": dict(metadata or {}),
            "retry_policy": {
                "max_attempts": attempts,
                "base_delay_seconds": delay,
                "multiplier": multiplier,
                "retryable_codes": list(retryable_codes or self.default_retryable_codes),
            },
            "worker": None,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "pipeline_run_manifest": None,
            "last_failure_classification": None,
            "last_error": None,
            "history": [],
        }
        return _public_job(_write_job(self.queue_root, job))

    def load(self, job_id: str) -> dict[str, Any]:
        return _public_job(_load_job(self.queue_root, job_id))

    def load_job(self, job_id: str) -> dict[str, Any]:
        return self.load(job_id)

    def list(self, states: list[str] | set[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        wanted = {_normalize_state(state) for state in states} if states else None
        jobs: list[dict[str, Any]] = []
        for path in self.queue_root.glob("*.json"):
            if not path.is_file():
                continue
            try:
                job = _read_json(path)
            except (OSError, json.JSONDecodeError, JobQueueError):
                continue
            if wanted and str(job.get("state")) not in wanted:
                continue
            jobs.append(job)
        return [_public_job(job) for job in sorted(jobs, key=_job_sort_key)]

    def list_jobs(self, states: list[str] | set[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        return self.list(states=states)

    def cancel(self, job_id: str, *, reason: str | None = None) -> dict[str, Any]:
        job = _load_job(self.queue_root, job_id)
        if str(job.get("state")) in TERMINAL_STATES:
            return _public_job(job)
        now = isoformat()
        job["state"] = "cancelled"
        job["updated_at"] = now
        job["finished_at"] = now
        job["cancelled_at"] = now
        job["cancel_reason"] = reason
        job.setdefault("history", []).append(
            {
                "event": "cancelled",
                "at": now,
                "reason": reason,
            }
        )
        return _public_job(_write_job(self.queue_root, job))

    def run_next(self, *, runner: CloneRunner | None = None, worker_id: str | None = None) -> dict[str, Any] | None:
        for job in self.list(states=["queued", "retry_wait"]):
            if not _is_due(job):
                continue
            try:
                return self.run_job(job["id"], runner=runner, worker_id=worker_id)
            except JobLockedError:
                continue
        return None

    def run(self, *, runner: CloneRunner | None = None, worker_id: str | None = None) -> dict[str, Any] | None:
        return self.run_next(runner=runner, worker_id=worker_id)

    def run_job(self, job_id: str, *, runner: CloneRunner | None = None, worker_id: str | None = None) -> dict[str, Any]:
        resolved_worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        with _job_lock(self.queue_root, job_id, resolved_worker_id):
            job = _load_job(self.queue_root, job_id)
            state = str(job.get("state") or "")
            if state in TERMINAL_STATES:
                return _public_job(job)
            if state == "retry_wait" and not _is_due(job):
                return _public_job(job)
            if state not in RUNNABLE_STATES:
                raise JobQueueError(f"job {job_id} is not runnable from state {state!r}")

            attempt = int(job.get("attempts") or 0) + 1
            started_at = isoformat()
            job["state"] = "running"
            job["attempts"] = attempt
            job["updated_at"] = started_at
            job["started_at"] = started_at
            job["finished_at"] = None
            job["next_retry_at"] = None
            job["worker"] = {
                "id": resolved_worker_id,
                "pid": os.getpid(),
                "started_at": started_at,
            }
            job.setdefault("history", []).append(
                {
                    "event": "started",
                    "attempt": attempt,
                    "at": started_at,
                    "worker_id": resolved_worker_id,
                    "output_dir": job.get("output_dir"),
                }
            )
            _write_job(self.queue_root, job)

            selected_runner = runner or self.runner or clone_reference_url
            try:
                result = self._run_clone(job, selected_runner)
            except Exception as exc:  # noqa: BLE001 - queue records must preserve worker failures.
                return self._finish_exception(job_id, exc, started_at, resolved_worker_id)
            return self._finish_result(job_id, result, started_at, resolved_worker_id)

    def _run_clone(self, job: dict[str, Any], runner: CloneRunner) -> dict[str, Any]:
        args = dict(job.get("clone_args") or {})
        args["url"] = job["url"]
        args["output_dir"] = job.get("output_dir")
        Path(str(job.get("output_dir"))).expanduser().resolve().mkdir(parents=True, exist_ok=True)
        result = runner(**args)
        if not isinstance(result, dict):
            raise JobQueueError("clone runner must return a JSON object")
        return result

    def _finish_exception(
        self,
        job_id: str,
        exc: Exception,
        started_at: str,
        worker_id: str,
    ) -> dict[str, Any]:
        job = _load_job(self.queue_root, job_id)
        finished_at = isoformat()
        if str(job.get("state")) == "cancelled":
            job["updated_at"] = finished_at
            job.setdefault("history", []).append(
                {
                    "event": "worker_exception_after_cancel",
                    "attempt": job.get("attempts"),
                    "at": finished_at,
                    "error": repr(exc),
                }
            )
            return _public_job(_write_job(self.queue_root, job))

        error = {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "repr": repr(exc),
        }
        can_retry = int(job.get("attempts") or 0) < _max_attempts(job)
        job["state"] = "retry_wait" if can_retry else "failed"
        job["updated_at"] = finished_at
        job["finished_at"] = finished_at
        job["last_error"] = error
        job["next_retry_at"] = _next_retry_at(job) if can_retry else None
        job.setdefault("history", []).append(
            {
                "event": "exception",
                "attempt": job.get("attempts"),
                "at": finished_at,
                "started_at": started_at,
                "worker_id": worker_id,
                "state": job["state"],
                "next_retry_at": job.get("next_retry_at"),
                "error": error,
            }
        )
        return _public_job(_write_job(self.queue_root, job))

    def _finish_result(
        self,
        job_id: str,
        result: dict[str, Any],
        started_at: str,
        worker_id: str,
    ) -> dict[str, Any]:
        job = _load_job(self.queue_root, job_id)
        finished_at = isoformat()
        if str(job.get("state")) == "cancelled":
            job["updated_at"] = finished_at
            job.setdefault("history", []).append(
                {
                    "event": "worker_result_after_cancel",
                    "attempt": job.get("attempts"),
                    "at": finished_at,
                    "result_keys": sorted(result.keys()),
                }
            )
            return _public_job(_write_job(self.queue_root, job))

        manifest = _pipeline_manifest_from_result(job.get("output_dir"), result)
        classification = _classification_from_manifest(manifest)
        next_state = _state_from_classification(job, classification)
        job["state"] = next_state
        job["updated_at"] = finished_at
        job["finished_at"] = finished_at
        job["result"] = result
        job["last_error"] = None
        job["last_failure_classification"] = classification or None
        job["next_retry_at"] = _next_retry_at(job) if next_state == "retry_wait" else None

        manifest_with_job = _write_manifest_job_metadata(
            job=job,
            manifest=manifest,
            started_at=started_at,
            finished_at=finished_at,
            worker_id=worker_id,
        )
        job["pipeline_run_manifest"] = manifest_with_job
        job.setdefault("history", []).append(
            {
                "event": "finished",
                "attempt": job.get("attempts"),
                "at": finished_at,
                "started_at": started_at,
                "worker_id": worker_id,
                "state": next_state,
                "next_retry_at": job.get("next_retry_at"),
                "failure_classification": classification or None,
                "pipeline_run_manifest_path": (manifest_with_job or {}).get("path"),
            }
        )
        return _public_job(_write_job(self.queue_root, job))


def enqueue(queue_root: str | Path, url: str, **kwargs: Any) -> dict[str, Any]:
    return JobQueue(queue_root).enqueue(url, **kwargs)


def load(queue_root: str | Path, job_id: str) -> dict[str, Any]:
    return JobQueue(queue_root).load(job_id)


def load_job(queue_root: str | Path, job_id: str) -> dict[str, Any]:
    return load(queue_root, job_id)


def list_jobs(queue_root: str | Path, states: list[str] | set[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    return JobQueue(queue_root).list(states=states)


def cancel(queue_root: str | Path, job_id: str, *, reason: str | None = None) -> dict[str, Any]:
    return JobQueue(queue_root).cancel(job_id, reason=reason)


def run_next(
    queue_root: str | Path,
    *,
    runner: CloneRunner | None = None,
    worker_id: str | None = None,
    **queue_kwargs: Any,
) -> dict[str, Any] | None:
    return JobQueue(queue_root, **queue_kwargs).run_next(runner=runner, worker_id=worker_id)


def run(
    queue_root: str | Path,
    *,
    runner: CloneRunner | None = None,
    worker_id: str | None = None,
    **queue_kwargs: Any,
) -> dict[str, Any] | None:
    return run_next(queue_root, runner=runner, worker_id=worker_id, **queue_kwargs)


def run_job(
    queue_root: str | Path,
    job_id: str,
    *,
    runner: CloneRunner | None = None,
    worker_id: str | None = None,
    **queue_kwargs: Any,
) -> dict[str, Any]:
    return JobQueue(queue_root, **queue_kwargs).run_job(job_id, runner=runner, worker_id=worker_id)


__all__ = [
    "CloneRunner",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRYABLE_CODES",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "JobLockedError",
    "JobNotFoundError",
    "JobQueue",
    "JobQueueError",
    "TERMINAL_STATES",
    "VALID_STATES",
    "cancel",
    "enqueue",
    "list_jobs",
    "load",
    "load_job",
    "run",
    "run_job",
    "run_next",
]
