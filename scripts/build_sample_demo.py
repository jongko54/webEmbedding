#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from html import escape
from pathlib import Path

from demo_artifacts import DemoCase, ROOT, load_demo_case


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>webEmbedding Sample Demo</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --panel: #171a21;
      --muted: #8f97aa;
      --text: #edf2ff;
      --accent: #83b0ff;
      --line: rgba(255,255,255,.1);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #1b2130, var(--bg) 58%);
      color: var(--text);
      min-height: 100vh;
    }}
    .frame {{
      width: 1600px;
      height: 900px;
      margin: 0 auto;
      padding: 32px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 24px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 24px;
    }}
    h1 {{
      margin: 0;
      font-size: 40px;
      line-height: 1;
      letter-spacing: -.04em;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 16px;
      max-width: 780px;
      line-height: 1.5;
      margin-top: 10px;
    }}
    .score-pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 10px 16px;
      color: var(--accent);
      font-weight: 700;
      background: rgba(131,176,255,.08);
      white-space: nowrap;
    }}
    .sample {{
      display: none;
      grid-template-rows: auto 1fr auto;
      gap: 18px;
      height: 100%;
    }}
    .sample.active {{ display: grid; }}
    .sample-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
    }}
    .sample-title {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -.03em;
    }}
    .compare {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      min-height: 0;
    }}
    .panel {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.015));
      border-radius: 24px;
      padding: 18px;
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 12px;
    }}
    .panel-label {{
      font-size: 14px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .12em;
    }}
    .panel img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: 16px;
      background: #0c0e13;
    }}
    .metrics {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--muted);
    }}
    .metric strong {{ color: var(--text); }}
    footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--muted);
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <div class="frame">
    <header>
      <div>
        <h1>webEmbedding Sample Copy Demo</h1>
        <div class="subtitle">Source screenshot vs generated bounded clone runtime. The deck rotates across persisted cases so stronger and weaker public examples are visible in one clip.</div>
      </div>
      <div class="score-pill">__ENGINE_LABEL__</div>
    </header>
    <main id="samples"></main>
    <footer>
      <div>Generated from persisted source and clone captures</div>
      <div id="pager"></div>
    </footer>
  </div>
  <script>
    const manifest = __MANIFEST__;
    const root = document.getElementById('samples');
    const pager = document.getElementById('pager');
    manifest.samples.forEach((sample, index) => {{
      const article = document.createElement('article');
      article.className = 'sample' + (index === 0 ? ' active' : '');
      article.dataset.index = String(index);
      const metrics = Object.entries(sample.metrics).map(([label, value]) => {{
        const n = typeof value === 'number' ? value.toFixed(2) : 'n/a';
        return `<span class="metric"><strong>${{label}}</strong> ${{n}}</span>`;
      }}).join('');
      article.innerHTML = `
        <div class="sample-head">
          <div>
            <div class="sample-title">${{sample.label}}</div>
            <div class="subtitle">Self-verify score ${{sample.score}} · verdict ${{sample.verdict}} · route ${{sample.renderer_route}}</div>
          </div>
          <div class="metrics">${{metrics}}</div>
        </div>
        <div class="compare">
          <section class="panel">
            <div class="panel-label">Source</div>
            <img src="${{sample.source_image}}" alt="${{sample.label}} source screenshot" />
          </section>
          <section class="panel">
            <div class="panel-label">Generated clone</div>
            <img src="${{sample.clone_image}}" alt="${{sample.label}} clone screenshot" />
          </section>
        </div>
      `;
      root.appendChild(article);
    }});
    let active = 0;
    function show(index) {{
      document.querySelectorAll('.sample').forEach((node, idx) => {{
        node.classList.toggle('active', idx === index);
      }});
      pager.textContent = `${{index + 1}} / ${{manifest.samples.length}}`;
    }}
    show(0);
    if (manifest.samples.length > 1) {{
      setInterval(() => {{
        active = (active + 1) % manifest.samples.length;
        show(active);
      }}, __ROTATION_MS__);
    }}
  </script>
</body>
</html>
"""


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _metric_value(case: DemoCase, name: str) -> float | None:
    value = case.metrics_by_score.get(name)
    if value is None:
        value = case.metrics_by_similarity.get(name)
    return value


def _build_manifest(cases: list[DemoCase], output_dir: Path) -> dict:
    manifest = {"samples": []}
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        source_copy = assets_dir / f"{case.case_id}-source.png"
        clone_copy = assets_dir / f"{case.case_id}-clone.png"
        _copy(case.source_image, source_copy)
        _copy(case.clone_image, clone_copy)

        manifest["samples"].append(
            {
                "id": case.case_id,
                "label": case.label,
                "score": case.score,
                "verdict": case.verdict,
                "renderer_route": case.renderer_route,
                "renderer_family": case.renderer_family,
                "source_image": f"./assets/{source_copy.name}",
                "clone_image": f"./assets/{clone_copy.name}",
                "metrics": {
                    "screenshot": _metric_value(case, "screenshot"),
                    "dom snapshot": _metric_value(case, "dom snapshot"),
                    "computed styles": _metric_value(case, "computed styles"),
                    "interaction states": _metric_value(case, "interaction states"),
                    "interaction trace": _metric_value(case, "interaction trace"),
                },
            }
        )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a rotating sample demo from persisted demo case directories.")
    parser.add_argument(
        "--case-dir",
        action="append",
        required=True,
        help="Case directory containing capture.json and reproduction artifacts. Repeat for multiple samples.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Optional label override for the matching --case-dir. Repeat in the same order as --case-dir.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / ".tmp" / "sample-demo"),
        help="Directory to write index.html, manifest.json, and copied assets into.",
    )
    parser.add_argument(
        "--engine-label",
        default="Universal engine: 88/100",
        help="Top-right pill label rendered into the demo header.",
    )
    parser.add_argument(
        "--rotation-ms",
        type=int,
        default=3500,
        help="Slide rotation interval in milliseconds when multiple samples are present.",
    )
    parser.add_argument(
        "--renderer-id",
        help="Optional renderer directory to load from self-verify artifacts. Defaults to auto-detect.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.label and len(args.label) != len(args.case_dir):
        raise SystemExit("When provided, --label must be repeated the same number of times as --case-dir.")

    labels = list(args.label) if args.label else [None] * len(args.case_dir)
    cases = [
        load_demo_case(case_dir, renderer_id=args.renderer_id, label=label)
        for case_dir, label in zip(args.case_dir, labels)
    ]
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest(cases, output_dir)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    html = (
        HTML_TEMPLATE
        .replace("__MANIFEST__", json.dumps(manifest))
        .replace("__ENGINE_LABEL__", escape(args.engine_label))
        .replace("__ROTATION_MS__", str(args.rotation_ms))
    )
    (output_dir / "index.html").write_text(html)
    print(output_dir / "index.html")


if __name__ == "__main__":
    main()
