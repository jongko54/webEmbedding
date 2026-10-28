# Changelog

## Unreleased

- Fixed site-profile false positives from page prose: framework detection now needs markup/asset markers (Spanish "vuelve" no longer marks python.org as a Vue app shell; "Shopify." or "readymag" in copy no longer selects those platforms).
- Stopped proposing a page as its own `direct-iframe` candidate when its headers block framing, and tightened `generic-embed` so links like `linkpreview.app` are not treated as reusable embeds.
- Refreshed universal benchmark expectations for live-site drift (wikipedia.org portal now longform; framer.com no longer ships canvas/WebGL markers).
- Pointed plugin homepage/privacy/terms URLs at the live hosted domain `webembedding-jongkos-mcp.vercel.app`.

## 0.3.11

- Added a `runtime-snapshot` renderer: a SingleFile-style serialization of the live DOM with authored/CSSOM stylesheets inlined, adoptedStyleSheets, open shadow roots as declarative shadow DOM, canvas pixels, form state, and localized image/font/stylesheet assets. Self-verify tries it first and skips scaffold renderers (and the Next build) when it is ready.
- Replaced the fixed post-load sleep with readiness signals: load event, network quiet, a lazy-load scroll pass, `document.fonts.ready`, and image decode. `wait_seconds` is now an upper bound.
- Scoped script-written pixel geometry (masonry, equal-height, slider widths) to the capture viewport so breakpoint renders fall back to authored layout.
- Captured breakpoint variants in parallel.
- Added a numpy/Pillow fast path for screenshot fingerprints (~80x faster on full-page screenshots, identical results; pure-Python fallback kept) and reported block SSIM / pixel-diff ratio alongside the existing screenshot score.
- Fixed the local MCP stdio transport: it now speaks standard newline-delimited JSON-RPC (Content-Length framing still accepted), negotiates the protocol version, and answers `ping`. Previously standard MCP clients received no response.
- Added MCP `instructions` (local and hosted) and rewrote the main tool descriptions around user intent, so agents know when to pick `clone_reference_url`, `audit_reference_url`, and friends.
- Added `llms.txt`, an agent quick-install and FAQ section in the README, refreshed benchmark numbers, and clearer registry/npm/plugin descriptions and keywords.
- Synced Claude/Codex plugin manifest versions with `package.json`.

## 0.3.10

- Fixed hosted Apps SDK review readiness for direct iframe reuse so public pages without frame blockers report exact/embed reuse instead of forcing rebuild routing.
- Fixed local MCP `classify_clone_mode` dispatch so submitted candidate/source arguments are passed through consistently.
- Moved the hosted Apps SDK endpoint to the `jongkos-projects` Vercel project at `https://webembedding-jongkos-mcp.vercel.app/mcp` and aligned widget metadata, submission package URLs, and smoke coverage.

## 0.3.9

- Added safe preflight `audit` CLI/MCP routing to report reuse readiness, local-capture needs, session requirements, manual-review cases, and blockers before browser capture.
- Added hosted Apps SDK readiness reports and local MCP handoff guidance to the remote intake endpoint.
- Expanded AI auto-selection golden prompts for audit/preflight, hosted intake, local clone/capture, and unsafe authorization cases.

## 0.3.8

- Hardened local MCP URL entrypoints to reject non-HTTP schemes and added tool annotations for safer agent approvals.
- Documented the hosted-vs-local sandboxing boundary and authenticated capture approval model.
- Bumped package, MCP registry, hosted endpoint, and plugin metadata versions for the next release.

## 0.3.7

- Added the primary hosted Apps SDK MCP alias at `https://webembedding-mcp.vercel.app/mcp`.
- Hosted public Apps SDK review pages for the app listing, privacy policy, terms, and icon.
- Aligned Apps SDK submission golden prompts with the hosted endpoint's read-only intake scope.
- Updated MCP Registry remote metadata to advertise the `/mcp` endpoint.

## 0.3.6

- Added a hosted Apps SDK intake MCP endpoint for low-risk source-first URL routing tools.
- Added remote MCP smoke coverage and Apps SDK submission prompts.
- Added MCP Registry remote transport metadata for the hosted endpoint.

## 0.3.5

- Added MCP Registry metadata and npm `mcpName` ownership verification.
- Added a direct `web-embedding mcp` stdio server command for registry and MCP client installs.
- Added repo-local Codex and Claude marketplace manifests.
- Aligned package, plugin, MCP server, and capture schema versions.
- Added AI auto-selection documentation, golden prompts, and local validation.

## 0.3.4

- Added a default opt-in telemetry collector endpoint and coarse execution-context labels.
