# webEmbedding v0.2 Architecture

## Goal

`webEmbedding` v0.2 should move from a **source-first intake plugin** into a **canonical webpage capture and reproduction system**.

The current bundle already has the right policy shape:

- inspect the reference URL
- discover candidate embed / preview / viewer / remix / export paths
- classify whether exact reuse is possible
- fall back to rebuild only when necessary

That is a strong v0.1 boundary, but it is not yet enough for the user goal of:

> "Given a prompt or reference URL, analyze the webpage fully and bring it over as faithfully as possible."

The v0.2 architecture should add:

- deeper acquisition of DOM, assets, runtime state, and interaction state
- a canonical capture bundle that all downstream steps can consume
- platform-specific adapters for reuse paths
- a verification loop that checks fidelity before shipping a reconstructed result

## Current Baseline

The current repo is already organized as a distributable Codex plugin with a single shared installer core:

- [`README.md`](../README.md)
- [`bundle/source-first-clone/mcp/server.py`](../bundle/source-first-clone/mcp/server.py)
- [`bundle/source-first-clone/skills/exact-clone-intake/SKILL.md`](../bundle/source-first-clone/skills/exact-clone-intake/SKILL.md)
- [`python/web_embedding/installer.py`](../python/web_embedding/installer.py)

Current MCP tool surface:

- `inspect_url`
- `discover_embed_candidates`
- `trace_runtime_sources`
- `classify_clone_mode`
- `generate_embed_snippet`

That surface is useful, but it stops at **discovery and classification**. v0.2 should add **capture, replay, and verification**.

## Recommended Architecture

### 1. Intent and Policy Layer

The first layer should normalize the user request and decide whether the task is:

- `embed`: use the original preview / viewer / iframe as-is
- `source`: reuse remix, export, or source-level assets
- `capture`: collect a canonical capture bundle for later reconstruction
- `rebuild`: reconstruct from captured data when no exact reuse path exists
- `block`: stop when the reference is private, licensed against reuse, or otherwise unavailable

This layer should stay small and deterministic. Its job is to decide the mode, not to do the heavy extraction.

### 2. Acquisition Layer

The acquisition layer should gather all data needed to reproduce a page faithfully.

It should combine:

- static fetch of HTML and metadata
- browser-driven capture for client-rendered content
- network inspection for assets and runtime URLs
- session-aware browsing when authentication matters
- state capture for hover, scroll, focus, and modal interactions

This is the main gap in the current implementation. Today, `trace_runtime_sources` can find browser-visible URLs, but it does not capture a full page model.

### 3. Canonical Capture Bundle

Every reference should be normalized into one portable capture format.

This bundle becomes the shared input for replay, reconstruction, QA, and archival.

### 4. Execution Layer

The execution layer consumes the capture bundle and tries the highest-fidelity path first:

- original embed / viewer
- original source / remix / export
- direct reconstruction from captured DOM and assets
- fallback code generation from screenshots or layout heuristics

### 5. Verification Layer

The system should not consider a reconstruction complete until it passes fidelity checks.

Verification should compare:

- structure
- typography
- color and spacing
- key viewport breakpoints
- important interaction states

### 6. Delivery Layer

The delivery layer packages the result for Codex installation and for external distribution.

The current `plugin + skill + MCP + installer` shape is good and should stay.

## Tool Taxonomy

v0.2 should keep the current tools, but expand them into a clearer taxonomy.

### Inspect Tools

Purpose: identify what the page is and how it is built.

- `inspect_url`
- `discover_embed_candidates`

Recommended additions:

- `inspect_dom_snapshot`
- `inspect_computed_styles`
- `inspect_network_manifest`

### Runtime Trace Tools

Purpose: discover client-rendered sources that are not visible in static HTML.

- `trace_runtime_sources`

Recommended additions:

- `trace_interactions`
- `trace_viewport_states`
- `trace_media_and_canvas_sources`

### Policy Tools

Purpose: decide whether a page can be reused exactly.

- `classify_clone_mode`

Recommended additions:

- `classify_license_and_provenance`
- `classify_embed_blockers`
- `classify_auth_requirements`

### Reuse and Replay Tools

Purpose: turn a discovered source into a usable target.

- `generate_embed_snippet`

Recommended additions:

- `generate_remix_snippet`
- `generate_capture_bundle`
- `replay_capture_bundle`

### Verification Tools

Purpose: measure fidelity and surface regressions.

Recommended additions:

- `compare_screenshots`
- `compare_layout_tree`
- `compare_interaction_states`
- `compare_bundle_to_reference`

## Canonical Capture Bundle

The canonical capture bundle should be a directory or archive with a strict top-level contract.

Example layout:

```text
capture/
  capture.json
  dom/
    snapshot.html
    accessibility.json
    layout-tree.json
  assets/
    images/
    fonts/
    media/
    scripts/
    styles/
  network/
    requests.har
    response-map.json
  screenshots/
    desktop-1440x900.png
    mobile-390x844.png
  interactions/
    default-state.json
    hover-state.json
    scroll-state.json
    modal-state.json
  provenance/
    source-urls.json
    license-signals.json
    blockers.json
```

### `capture.json` shape

`capture.json` should be the manifest for everything else in the bundle.

Recommended top-level fields:

- `schemaVersion`
- `reference`
- `resolvedUrl`
- `captureMode`
- `policy`
- `platform`
- `document`
- `assets`
- `states`
- `verification`
- `provenance`

Suggested field meanings:

- `reference`: the original user-supplied URL or prompt-derived target
- `resolvedUrl`: the final URL after redirects and browser navigation
- `captureMode`: `embed`, `source`, `capture`, `rebuild`, or `block`
- `policy`: license, ownership, iframe, and auth decisions
- `platform`: detected platform such as Spline, Framer, Webflow, or generic SPA
- `document`: DOM, title, metadata, and accessibility info
- `assets`: extracted images, fonts, scripts, videos, SVGs, and canvas references
- `states`: interactive states and the actions needed to reach them
- `verification`: screenshot diff or layout diff summaries
- `provenance`: where each piece came from

### Data rules

- The bundle should be deterministic where possible.
- Every extracted artifact should retain provenance.
- The capture should include enough information to reproduce at least the important visible states.
- The bundle should be readable by both local tooling and hosted tooling.

## Phased Roadmap

### Phase 0: Harden v0.1

Goal: make the current plugin safer and more precise without changing its shape.

- tighten license and blocker checks
- improve URL candidate heuristics
- add minimal tests for classification and candidate discovery
- harden installer integrity checks

### Phase 1: Canonical Capture

Goal: capture the full page model instead of just source hints.

- add DOM snapshot capture
- add computed style extraction
- add network manifest capture
- add screenshot capture for at least desktop and mobile
- add a `capture.json` schema

### Phase 2: Platform Adapters

Goal: reuse the best path for the detected platform.

- Spline adapter
- Framer adapter
- Webflow adapter
- generic iframe / embed adapter
- generic SPA adapter

### Phase 3: Reconstruction and Replay

Goal: reconstruct from capture when exact reuse is unavailable.

- replay captured bundles
- generate HTML / Next.js scaffolds from captured DOM and assets
- support screenshot or layout-driven fallback generation

### Phase 4: Fidelity QA Loop

Goal: stop shipping approximate results as exact matches.

- compare against reference screenshots
- compare layout trees
- compare key states and interactions
- iterate until a fidelity threshold is met or a rebuild disclaimer is emitted

### Phase 5: Hosted Backend Option

Goal: support pages that need persistent sessions or heavier browser instrumentation.

- session-aware capture backend
- remote browser execution
- stronger anti-bot tolerance
- optional queue-based capture jobs

## What To Keep

The current packaging strategy is already correct:

- `plugin` for distribution
- `skill` for orchestration
- `MCP` for capability
- `installer` for local deployment

Do not split these into unrelated products. v0.2 should deepen the engine, not fragment the delivery model.

## Non-Goals

v0.2 should not try to do all of the following at once:

- universal DRM bypass
- unauthorized private source extraction
- generic website mirroring without policy gates
- exact visual cloning without a provenance trail

The product should stay source-first and policy-aware.

## Summary

The current repo is a good v0.1 source-first intake system.

The right v0.2 move is to add:

- a canonical capture bundle
- deep browser acquisition
- platform adapters
- reconstruction and replay
- fidelity verification

That gives `webEmbedding` a clear path from "find the original source" to "reliably reproduce the page with measurable fidelity."
