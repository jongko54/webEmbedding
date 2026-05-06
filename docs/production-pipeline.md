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

The current readiness gate validates that this manifest is part of the required production artifact contract. Queue storage can be SQLite, a filesystem directory, or an external queue as long as it preserves the same fields.

## Report Handoff

Every completed job should produce one machine-readable report and one preview target:

- report: route, policy, evidence limitations, failure/action codes, scores, and next action
- preview: exact iframe/source reuse page or bounded rebuild preview URL

`manual_review` is an acceptable output state for app shells, canvas/WebGL, public app gates, and authenticated surfaces. It is not a successful no-review clone state.

## Current Gate

`npm run check:production-readiness:local` validates that the route corpus, failure taxonomy, CI wiring, policy docs, and production gate manifest are present and consistent. It is intentionally a lightweight gate; heavier live clone score checks should run as scheduled or release-blocking jobs.
