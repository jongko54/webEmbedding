# Production Pipeline

This document defines the minimum operating model for running webEmbedding as a production clone pipeline instead of an ad hoc local tool.

## Job Lifecycle

Every URL should be wrapped in a job record with these states:

- `queued`: URL and policy inputs accepted, no capture started.
- `running`: a worker owns the job and is writing artifacts under one output root.
- `retry_wait`: the run hit a retryable condition and has a scheduled retry.
- `succeeded`: verification and readiness gates passed.
- `failed`: retries were exhausted or a non-policy execution error remained.
- `blocked`: policy says the job must not continue.
- `needs_session`: the target requires authenticated browser evidence or user-supplied screenshots.
- `manual_review`: bounded output exists, but evidence gaps prevent no-review acceptance.

## Retry Policy

The first production queue can stay simple:

- `max_attempts`: `2`
- retryable: network timeouts, browser launch flake, `network-replay-limited`, `network-request-failures`
- non-retryable without new user input: `blocked-by-policy`, `public-app-gate`, `native-app-target-required`, `auth-session-missing`

Retries must preserve the previous `failure_classification`, logs, and artifact paths. A later successful attempt should not delete failed-attempt evidence.

## Artifact Layout

Each `clone` run with an `output_dir` writes a stable `pipeline-run-manifest.json` beside the existing capture and reproduction artifacts. The manifest includes:

- `run_id`
- input URL and normalized final URL
- policy inputs and resulting mode
- route hints and `failure_classification`
- attempt count and worker timestamps
- artifact paths for `capture.json`, `network/manifest.json`, `network/har.json`, `reproduction/plan.json`, `reproduction/evidence-limitations.json`, and `reproduction/self-verify/summary.json`
- verification scores and breakpoint status
- redaction status for session, headers, cookies, query strings, and form bodies

The current readiness gate validates that this manifest is part of the required production artifact contract. The repository now includes a filesystem job queue in `source_first_clone.job_queue`; it persists each job as JSON, claims work with per-job lock files, records retry history, annotates `pipeline-run-manifest.json` with worker metadata, and exposes `enqueue`, `list`, `status`, `cancel`, `run-next`, and `run-job`.

```bash
node ./bin/web-embedding.mjs queue enqueue \
  --queue-root ./.tmp/clone-job-queue \
  --url https://www.mozilla.org/ \
  --output-dir ./.tmp/queued-mozilla

node ./bin/web-embedding.mjs queue run-next \
  --queue-root ./.tmp/clone-job-queue \
  --worker-id local-worker-1
```

Queue storage can later move to SQLite or an external queue as long as it preserves the same job fields and terminal-state semantics.

## Report Handoff

Every completed job should produce one machine-readable report and one preview target:

- report: route, policy, evidence limitations, failure/action codes, scores, and next action
- preview: exact iframe/source reuse page or bounded rebuild preview URL

`manual_review` is an acceptable output state for app shells, canvas/WebGL, public app gates, and authenticated surfaces. It is not a successful no-review clone state.

## HAR Replay

Captured network artifacts now have a replay gate beyond the readiness summary. `source_first_clone.har_replay` loads standard HAR, near-HAR, or `network/manifest.json`, indexes requests by method, normalized URL, and request-body hash, and returns deterministic replay responses plus a `network/replay-report.json` audit.

Run a local smoke:

```bash
npm run check:har-replay:local
```

Replay specific requests from a captured HAR:

```bash
node ./bin/web-embedding.mjs har-replay \
  --har ./.tmp/clone-mozilla/network/har.json \
  --request GET https://www.mozilla.org/ \
  --out ./.tmp/clone-mozilla/network/replay-report.json \
  --strict
```

The engine supports HARs that include response bodies. When a captured HAR only contains status/header metadata, the report still matches the request but marks `body_available=false` so downstream workers cannot mistake metadata-only replay for full response replay.

## Authenticated Dashboard Corpus

Live authenticated dashboard checks are manifest-driven so session material stays outside the repo. Use `docs/authenticated-dashboard-corpus.example.json` as the template and point `storage_state_path` or `user_data_dir` at local paths through environment variables.

Run a schema-only smoke without resolving credentials:

```bash
python3 ./scripts/benchmark_authenticated_corpus.py \
  --manifest docs/authenticated-dashboard-corpus.example.json \
  --validate-only
```

Run a no-auth dry run that records which items are runnable versus `needs_session`:

```bash
python3 ./scripts/benchmark_authenticated_corpus.py \
  --manifest docs/authenticated-dashboard-corpus.example.json \
  --dry-run
```

When session paths exist, the runner executes `web-embedding clone` per item, checks optional score thresholds and captured runtime selectors, then writes `authenticated-dashboard-corpus-report.json` with `total`, `runnable`, `skipped`, `succeeded`, `failed`, and `needs_session` counts. Reported commands redact session path arguments; the credential files themselves must remain outside version control.

## Current Gate

`npm run check:production-readiness:local` validates that the route corpus, failure taxonomy, CI wiring, policy docs, and production gate manifest are present and consistent. It is intentionally a lightweight gate; heavier live clone score checks should run as scheduled or release-blocking jobs.
