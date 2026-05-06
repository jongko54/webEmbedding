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
