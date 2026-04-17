# webEmbedding v0.3 Playwright Integration

## Goal

`webEmbedding` v0.3 should turn the current `source-first-clone` plugin into a **session-aware capture system**.

The objective is not to replace the existing source-first policy. The objective is to make the acquisition layer strong enough to capture:

- authenticated pages
- client-rendered pages
- interaction states
- asset manifests
- reusable browser session data

## What Stays The Same

The existing plugin should remain the orchestration boundary.

Keep in place:

- `exact-clone-intake` as the policy skill
- `inspect_url`, `discover_embed_candidates`, `trace_runtime_sources`, `classify_clone_mode`, `generate_embed_snippet`
- the plugin packaging model
- the source-first decision rule: `embed -> source -> capture -> rebuild -> block`

v0.3 should extend that system, not replace it.

## What Playwright Adds

Playwright is the browser acquisition layer.

It should be used for:

- session attach to an existing browser or persistent profile
- storage state capture and restore
- authenticated navigation
- DOM and accessibility snapshots
- network and asset capture
- viewport and interaction-state capture

This fills the main gap in v0.2: the system can discover references, but it cannot yet capture a page as a faithful browser session.

## Session Model

The long-term design has three browser entry modes.

### 1. Persistent Profile

Use a persistent user data directory when a stable local profile is enough.

Best for:

- repeated captures
- logged-in workflows
- low-friction local testing

### 2. Storage State

Use Playwright storage state as the portable session boundary.

Best for:

- authenticated capture pipelines
- reproducible CI runs
- handoff between machines

### 3. CDP Attach

Attach to an already running browser session when the target page must be observed in a real user profile.

Best for:

- existing Chrome sessions
- pages that depend on current cookies or extensions
- pages that behave differently in a live browser

Note: the current `v0.3.1` baseline in this repo implements persistent profile and storage-state flows first. CDP attach is still a planned extension, not a shipped claim.

## Capture Artifacts

The capture output should become a canonical bundle.

Minimum artifacts:

- `capture.json`
- DOM snapshot
- accessibility tree
- computed styles summary
- network trace or HAR
- asset inventory
- viewport screenshots
- interaction-state screenshots
- storage state

Recommended bundle structure:

```text
capture/
  capture.json
  dom/
  accessibility/
  styles/
  network/
  assets/
  screenshots/
  interactions/
  session/
```

`capture.json` should be the manifest for everything else.

## Integration Boundary

The current plugin should not grow browser-specific logic directly into the policy layer.

Boundary rules:

- `source-first-clone` decides what mode to use
- Playwright performs capture and session handling
- the capture bundle is passed back to the plugin as input
- verification stays separate from acquisition

This keeps the plugin lightweight and keeps browser churn isolated.

## Proposed Flow

1. Receive a URL or prompt.
2. Run the existing source-first classifier.
3. If exact reuse is not available, open a Playwright session.
4. Attach to persistent profile, storage state, or CDP session.
5. Capture the page into a canonical bundle.
6. Pass the bundle into reconstruction or replay.
7. Run fidelity verification on key states and breakpoints.

## Non-Goals For v0.3

v0.3 should not try to solve everything at once.

Do not attempt to:

- build a full website mirroring engine
- replace the policy skill with browser code
- bake every platform adapter into the core plugin
- make reconstruction and verification part of the browser layer

## Success Criteria

v0.3 is successful if the system can:

- attach to a real browser session when needed
- preserve login state across captures
- capture repeatable page artifacts
- hand those artifacts to the existing source-first plugin boundary
- improve fidelity on authenticated and highly dynamic pages

## Open Questions

- should session attach live inside the local plugin bundle or a hosted backend
- which capture artifact is required versus optional
- how much of the bundle should be standardized before adapter-specific metadata
- whether verification should use screenshots only or include DOM and layout diffs
