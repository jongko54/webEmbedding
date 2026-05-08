---
name: "exact-clone-intake"
description: "Use when a user gives a URL and wants the page copied, cloned, embedded, captured, source-routed, or reconstructed with high fidelity. Trigger on Korean requests like 그대로 가져와줘 or 완전 똑같이. Prefer exact reuse when possible; otherwise capture structured evidence, build a bounded frontend scaffold, and verify the result."
---

# Exact Clone Intake

Treat requests like `그대로 가져와줘`, `완전 똑같이`, `same`, `exact`, `as-is`, and `clone this` as source-first clone requests.

Do not use this skill for generic URL summarization, data scraping, localhost debugging, original page design, package maintenance, or requests to bypass auth, paywalls, captcha, ownership, or license boundaries.

This skill is paired with the `source-first-clone` MCP server. It is meant for general AI coding workflows, not a single framework. The important behavior is: inspect first, capture evidence, reuse when allowed, rebuild only when needed, and verify the output instead of trusting prompt-only generation.

## Workflow

1. Inspect the reference URL before writing code.
2. Use MCP tools in this order:
   - `clone_reference_url` when you want the full URL-to-clone workflow in one pass
   - `detect_runtime_capabilities` when session-aware capture might be needed
   - `inspect_url`
   - `discover_embed_candidates`
   - `trace_runtime_sources` when static HTML is not enough
   - `classify_clone_mode`
   - `capture_reference_bundle` when exact reuse is unclear or blocked
   - `build_rebuild_scaffold` when exact reuse is blocked and you need bounded reconstruction artifacts
   - `build_reproduction_bundle` after capture when you need an exact reuse output package
   - `plan_reproduction_path`
   - `generate_embed_snippet` only when an actual embed path exists
   - `verify_fidelity_report` when comparing a reproduced result
3. Prefer these outcomes in order:
   - direct iframe reuse of the original page when frameable
   - original embed or preview
   - original remix, export, or source
   - bounded rebuild from captured evidence with a fidelity report
4. Respect license and ownership signals. If the page is not clearly reusable, say so before cloning.

## Output rules

- Do not call a rebuild exact unless it is actually direct source reuse.
- If an original preview or embed exists, use it instead of recreating the page.
- If the original source is private, explain what is missing and what permission or export is needed.
- When a rebuild is used, report the verification score and the strongest remaining gaps.
- For public demos, prefer neutral examples such as documentation or public landing pages over brand-sensitive clone claims.

## Tool notes

- `trace_runtime_sources` is especially useful for pages that hide the real scene URL behind client-side rendering. Use `user_data_dir` or `storage_state_path` when authentication matters.
- `capture_reference_bundle` persists DOM, runtime HTML, screenshots, computed styles, CSS analysis, assets, network metadata, interaction states, traces, accessibility data, and optional storage state.
- `build_rebuild_scaffold` is for frame-blocked or source-blocked references. Use it to output reusable frontend reconstruction artifacts plus a layout summary instead of pretending prompt-only output is exact.
- `clone_reference_url` is the fastest path when the user has pasted a link and wants the full source-first clone pipeline executed immediately.
- `plan_reproduction_path` is useful when the policy decision is clear but the build sequence is not.
- `generate_embed_snippet` is for fast HTML or component integration once the exact source URL is known.
- `verify_fidelity_report` should be used honestly: it is bounded to persisted artifacts and does not claim pixel-perfect equivalence.
