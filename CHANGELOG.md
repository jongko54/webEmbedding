# Changelog

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
