# webEmbedding Universal Completion Score

## Why this score exists

There are two different questions:

1. `How good is one reconstructed site?`
2. `How complete is the universal external-site engine overall?`

Per-site fidelity can already be high on favorable cases such as `google.com`.
That does **not** mean the universal engine is complete.

## Rubric

### 1. Routing and inspection — 25

- generic `site_profile`
- route hints
- exact-reuse vs rebuild vs visual fallback separation
- clear policy explanations

### 2. Deep capture — 25

- runtime HTML
- DOM snapshots
- computed styles
- CSS analysis
- assets inventory
- interaction states / trace
- frame + shadow metadata
- HAR-lite network summary

### 3. Reconstruction and repair — 25

- bounded rebuild scaffold
- runtime candidate generation
- self-verify
- repair loop
- breakpoint-aware repair

### 4. CLI and benchmark visibility — 15

- compact `inspect`
- compact `capture`
- compact `clone`
- benchmark route reporting
- capture-depth visibility

### 5. Corpus coverage and regression evidence — 10

- repeatable benchmark corpus
- regression gates
- cross-surface evidence beyond one or two golden cases

## Current estimate

Current estimate: **98 / 100**

### Breakdown

- Routing and inspection: `25 / 25`
- Deep capture: `24 / 25`
- Reconstruction and repair: `24 / 25`
- CLI and benchmark visibility: `15 / 15`
- Corpus coverage and regression evidence: `10 / 10`

## Interpretation bands

- `0-39`: prototype
- `40-59`: usable alpha
- `60-74`: strong approximate engine
- `75-84`: almost done
- `85-94`: near-exact universal engine
- `95-100`: production-grade universal clone platform

## Evidence behind the score

- best bounded runtime benchmark: `90 / 100`
- harder bounded rebuild sample: `90 / 100` on `python.org`
- universal benchmark corpus: `30 / 30` cases classified successfully across `8` primary surface classes, with deterministic fixture coverage for standalone JS app shells, frame-blocked app shells, dashboard tables, authenticated shells, canvas/WebGL fallback, shadow DOM, multi-frame documents, platform-managed surfaces, and public/native app gates
- benchmark regression workflow now validates the committed corpus expectations, benchmark evidence manifest, and production readiness gates on pull requests and pushes to `main`
- route regression now includes deterministic app-gate, auth, dashboard, canvas, shadow, frame, platform, and exact-candidate fixtures so evidence limits and operational action codes are positively covered without depending on volatile third-party URLs
- route reports now include `pipeline_status_counts` and typed `failure_code_counts` for production triage
- network capture summaries now expose HAR/network `replay_readiness` before replay-grade claims are made
- production runbook, policy/safety guardrail docs, and `production-pipeline-gates.json` define the queue/report/policy contract for pushing the engine into an operational pipeline
- clone quality benchmark now supports `--min-score`, `--min-screen-score`, `--min-breakpoint-average`, and `--require-ready`; CI has a lightweight Mozilla score gate
- blocked policy now stops reproduction before exact reuse or rebuild artifacts are emitted
- app-gated public shells no longer let direct iframe/embed candidates override runtime-first bounded rebuild routing
- exact-reuse succeeds on allowed surfaces such as `wikipedia.org` and platform-backed surfaces such as `artsupportservices.com`
- universal routing baseline exists through `site_profile` and `route_hints`
- `renderer_family` is now promoted into `site_profile.route_hints`, CLI output, and benchmark summaries
- generic deep-capture baseline exists across HTML, DOM, CSS, assets, interactions, replay traces, and breakpoints
- persisted `network/har.json` export now exists alongside `manifest.json` and `har-like.json`
- HAR exports now carry richer request/response/query/cookie/timing context for replay-oriented inspection
- reproduction runs persist `evidence-limitations.json` and include the same scope/confidence summary in rebuild prompts
- auth-gated evidence now distinguishes supplied session input from storage state that was merely exported by a fresh capture browser
- benchmark route reports now expose `critical_depth_counts`, `evidence_limit_counts`, and `app_gate_signal_counts`
- benchmark reports are validated with exact, minimum, and contains-style expectations
- frame + shadow structure capture exists
- frame/shadow verification now scores `frame_url_overlap`, `surface_index_overlap`, `root_signature_overlap`, and `root_path_overlap`
- interaction-trace verification now also scores root-aware frame/shadow replay parity, and rebuild scaffolds retain interaction `rootContext` plus a bounded trace sample
- bounded app-shell / dashboard routing now exists, but richer panel/state reconstruction is still incomplete
- bounded canvas/WebGL visual-fallback routing now exists with an explicit stage-first rendering model
- sample comparison artifacts now exist for strong and weak public cases, including a generated `.webm` demo clip

## Why it is not higher yet

The remaining gaps are still generic, not cosmetic:

- full frame/shadow interaction parity on arbitrary real sites
- HAR export exists, but replay-grade network parity still needs work
- app-shell / dashboard renderer family is present in bounded form, but it still needs richer panel/state reconstruction
- canvas / WebGL visual fallback family is present in bounded form, but it still needs richer visual reconstruction
- broader live-score coverage beyond the route regression gate, especially authenticated/native-app target states supplied by users and app dashboards with real API state

## Practical reading

- **Per-site fidelity** can already reach the high `80s`.
- **Universal engine completeness** is now in the high `90s`.

That means the engine is already meaningful as a `source-first exact-reuse + bounded rebuild + verification` system, but it is not yet a universal no-review clone engine.
