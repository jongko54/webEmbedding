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

Current estimate: **84 / 100**

### Breakdown

- Routing and inspection: `24 / 25`
- Deep capture: `22 / 25`
- Reconstruction and repair: `21 / 25`
- CLI and benchmark visibility: `12 / 15`
- Corpus coverage and regression evidence: `5 / 10`

## Interpretation bands

- `0-39`: prototype
- `40-59`: usable alpha
- `60-74`: strong approximate engine
- `75-84`: almost done
- `85-94`: near-exact universal engine
- `95-100`: production-grade universal clone platform

## Evidence behind the score

- best bounded runtime benchmark: `88 / 100`
- harder bounded rebuild sample: `61 / 100`
- universal benchmark corpus: `4 / 4` sites classified successfully, with `2` exact-reuse and `2` bounded-rebuild outcomes
- exact-reuse succeeds on allowed surfaces such as `wikipedia.org` and platform-backed surfaces such as `artsupportservices.com`
- universal routing baseline exists through `site_profile` and `route_hints`
- `renderer_family` is now promoted into `site_profile.route_hints`, CLI output, and benchmark summaries
- generic deep-capture baseline exists across HTML, DOM, CSS, assets, interactions, replay traces, and breakpoints
- persisted `network/har.json` export now exists alongside `manifest.json` and `har-like.json`
- frame + shadow structure capture exists
- bounded app-shell / dashboard routing now exists, but richer panel/state reconstruction is still incomplete

## Why it is not higher yet

The remaining gaps are still generic, not cosmetic:

- full frame/shadow interaction parity on arbitrary real sites
- HAR export exists, but richer initiator/body/timing parity still needs work
- app-shell / dashboard renderer family is present in bounded form, but it still needs richer panel/state reconstruction
- canvas / WebGL visual fallback family
- broader corpus benchmark CI and regression gates

## Practical reading

- **Per-site fidelity** can already reach the high `80s`.
- **Universal engine completeness** is in the low `80s`.

That means the engine is already meaningful as a `source-first exact-reuse + bounded rebuild + verification` system, but it is not yet a universal no-review clone engine.
