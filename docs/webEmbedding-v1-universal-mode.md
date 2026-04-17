# webEmbedding v1 Universal Mode

## Goal

Turn `webEmbedding` from a strong `source-first exact-reuse + bounded rebuild` engine into a system that can accept **arbitrary external URLs** and automatically choose the strongest available path:

1. exact reuse when a safe upstream surface exists
2. platform-source import when the site is backed by a known builder
3. deep runtime capture when the site is app-like or auth-gated
4. bounded rebuild when reuse is blocked
5. visual fallback when the site is canvas/WebGL-heavy

This is not “one renderer for all sites.” It is **universal routing across multiple site classes**.

## Current Baseline

The repo already does these well:

- platform-aware hints for `Spline`, `Figma`, `Framer`, and `Webflow`
- frame-policy inspection
- session-aware runtime capture
- persisted capture bundles
- bounded rebuild scaffolds
- bounded self-verify and repair loops
- generic surface classification via `site_profile`
- generic deep-capture summaries for DOM, CSS, assets, and network
- same-origin frame and open-shadow baseline capture metadata
- compact reporting for frame/shadow/network depth so CLI and benchmarks show the current capture envelope
- HAR-lite network summaries for redirects, timing buckets, header presence, and body-availability hints
- persisted standard `har.json` export baseline alongside `har-like.json`
- app-shell / dashboard-like surfaces now route to a dedicated shell-oriented bounded rebuild mode instead of the same compact landing-page compression
- canvas/WebGL-like surfaces now route to an explicit bounded visual-fallback family with stage-first constraints
- verification still treats frame/shadow interaction parity as a bounded signal, not full replay parity
- benchmark corpus runs can now be repeated against a small generic corpus file for route/depth regression checks
- benchmark reports also surface `route_quality_counts`, `renderer_family_counts`, and `depth_presence_counts` for generic regression checks

The `site_profile` layer now exists, but it still needs broader coverage for shell-like app surfaces, richer benchmark feedback, and more adapter-specific routing signals.

## New Universal Layer

The current worktree introduces a `site_profile` layer.

`site_profile` is intended to answer:

- what kind of surface is this?
- how deep should capture go?
- which renderer family should be preferred?
- where are the hard failure modes?

### Primary surfaces

- `platform-managed-surface`
- `frame-blocked-app-surface`
- `authenticated-app-surface`
- `js-app-shell-surface`
- `multi-frame-document-surface`
- `canvas-or-webgl-surface`
- `longform-content-surface`
- `static-document`

### Route hints

The classifier emits route hints such as:

- `acquisition_profile`
  - `platform-aware-source-first`
  - `browser-deep-capture`
  - `session-aware-browser-capture`
  - `frame-aware-capture`
  - `visual-runtime-capture`
  - `static-first`
- `renderer_route`
  - `exact-reuse`
  - `platform-source-or-bounded-rebuild`
  - `runtime-first-bounded-rebuild`
  - `visual-fallback-rebuild`
  - `bounded-rebuild`
- `critical_depths`
  - `dom`
  - `computed-styles`
  - `runtime-html`
  - `network`
  - `frame-documents`
  - `shadow-dom`
  - `canvas-surface`
  - `session-state`

## What Is Still Missing for True Universal Support

### 1. Capture depth

Current blockers:

- frame documents are detected and counted, but frame-local interaction/state parity is still shallow
- open shadow roots are detected and traversed, and root-aware parity now includes frame/surface identity signals, but full shadow-aware replay and diffing are still partial
- HAR export baseline exists, but richer initiator/body/timing parity is still missing
- sampled computed styles, not a full-tree style graph
- no app-state serialization beyond bounded interaction replay
- no canvas/WebGL introspection beyond screenshots

### 2. Renderer families

Current blockers:

- bounded rebuild is still optimized for compact marketing/document surfaces
- app-shell renderer routing exists, but the generic shell renderer still needs richer panel/state reconstruction
- a bounded visual-fallback family now exists for canvas-heavy or scene-heavy sites, but it is still screenshot-led rather than scene-aware

### 3. Verification depth

Current blockers:

- bounded artifact verification is strong but still not a full browser-to-browser parity proof
- breakpoint verification is stronger than breakpoint repair
- repair loops are still local and bounded

### 4. Site adapters

Current blockers:

- only four explicit platform adapters are present
- there is no registry for broader site families
- candidate normalization is still regex-heavy for unknown platforms

## Recommended v1 Build Order

1. `Frame + shadow interaction parity`
   - structure discovery is present
   - root-aware replay/state fidelity still needs deeper coverage
2. `Richer HAR-grade capture`
   - HAR-lite and a persisted `har.json` baseline exist today
   - richer timing, initiator, redirect, and body parity still need work
3. `Renderer routing by surface class`
4. `App-shell renderer family`
5. `Canvas/WebGL visual fallback path`
6. `Breakpoint-aware structural repair`
7. `Corpus-based benchmark CI`

## Product Positioning

With the current baseline plus universal routing, `webEmbedding` is suitable for:

- expert-operated reconstruction workflows
- internal tooling
- enterprise pipelines where exact reuse is preferred and bounded rebuild is reviewed

It is still not sufficient to promise:

- “paste any URL and get a perfect clone”
- “every site will rebuild above 85 automatically”

Universal mode should therefore be described as:

`automatic route selection across external-site classes, with exact reuse when possible and bounded reconstruction otherwise`
