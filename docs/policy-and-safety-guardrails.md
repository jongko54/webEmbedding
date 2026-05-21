# Policy And Safety Guardrails

webEmbedding is a source-first clone and reconstruction workflow. Production use needs explicit guardrails because exact reuse, bounded rebuilds, authenticated sessions, and captured network artifacts have different risk levels.

## Permission And License

Before a job is treated as production-ready, the caller must provide one of:

- owned property or explicit permission
- an embeddable source/preview route that the target intentionally exposes
- license evidence allowing reuse
- an internal review decision that bounded reconstruction is acceptable

If `license_text` or policy input indicates "all rights reserved", copyright-only reuse, or another blocked condition, the pipeline must stop in `blocked` state. A blocked policy must prevent exact reuse and rebuild artifacts from being presented as usable output.

## Robots And ToS

The current engine does not automatically enforce `robots.txt` or site ToS. Production callers must record whether robots and ToS were reviewed. If the site disallows the relevant capture or reuse path, the job should move to `blocked` or `manual_review` rather than `succeeded`.

## Session And Secrets

Authenticated capture is allowed only with user-supplied browser evidence or storage state. Exporting a fresh browser storage state during capture does not prove authenticated access.

Session artifacts must be treated as sensitive:

- do not upload raw storage state to public artifacts
- do not expose cookies or authorization headers in user-facing reports
- do not claim private app fidelity from a public login shell
- mark app-gated or native-app-led pages as `needs_session` or `manual_review`

## Current Sandboxing And Approvals

The hosted Apps SDK endpoint is intentionally narrow. It provides read-only URL inspection, embed/source candidate discovery, clone-mode classification, and planning helpers. It accepts only absolute `http` and `https` URLs, does not run Playwright, does not read local paths, does not accept browser profiles or storage state, and does not persist capture artifacts.

The local stdio MCP and CLI are the only surfaces that run browser capture, filesystem queues, HAR replay, bounded rebuild scaffolds, and one-shot clone workflows. Those tools run under the user's local agent and filesystem permissions, so the caller is responsible for agent approvals and for choosing trusted `output_dir`, `queue_root`, `har_path`, `storage_state_path`, and `user_data_dir` values.

Local URL entrypoints must reject non-HTTP schemes such as `file://`. This prevents the tool from being used as a local file reader through URL-shaped input. Browser session inputs are explicit user-supplied evidence, not implicit credential collection.

Recommended production hardening before shared hosted capture workers:

- run capture workers in isolated containers or VMs
- enforce per-run output roots and deny arbitrary local path access
- require domain allowlists or recorded approval for authenticated targets
- use ephemeral browser profiles by default
- redact or isolate HAR, cookies, storage state, screenshots, and captured HTML before sharing artifacts
- add quotas, audit logs, and manual review for account, admin, checkout, payment, paywall, captcha, and private dashboard routes

## PII And Network Artifacts

HAR, query strings, cookies, request headers, response headers, and form bodies can contain PII or secrets. Production storage should redact or isolate:

- `cookie` and `set-cookie`
- `authorization`
- email, password, token, key, secret, session, and code query parameters
- POST bodies from login, checkout, payment, account, or admin routes

The current gate requires this risk to be documented. A full production queue should add a redaction pass before sharing artifacts outside the trusted worker environment.

## Exact Reuse Vs Bounded Rebuild

Reports and previews must clearly distinguish:

- `exact reuse`: original frame/source/embed route is reused
- `source reuse`: original source/export/remix evidence is used
- `bounded rebuild`: generated frontend approximates the captured evidence
- `visual fallback`: canvas/WebGL or native-like UI is reconstructed from screenshots and limited DOM evidence

Bounded rebuilds are not ownership or permission bypasses. Native app fidelity must not be claimed from public web app-gate evidence alone.
