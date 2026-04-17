#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".tmp" / "sample-demo-20260417"


SAMPLES = [
    {
        "id": "google",
        "label": "Google",
        "source_capture": ROOT / ".tmp" / "demo-google-20260417" / "capture.json",
        "clone_summary": ROOT / ".tmp" / "demo-google-20260417" / "reproduction" / "self-verify" / "summary.json",
        "clone_verification": ROOT / ".tmp" / "demo-google-20260417" / "reproduction" / "self-verify" / "renderers" / "next-runtime-app" / "verification.json",
    },
    {
        "id": "python",
        "label": "Python.org",
        "source_capture": ROOT / ".tmp" / "demo-python-20260417" / "capture.json",
        "clone_summary": ROOT / ".tmp" / "demo-python-20260417" / "reproduction" / "self-verify" / "summary.json",
        "clone_verification": ROOT / ".tmp" / "demo-python-20260417" / "reproduction" / "self-verify" / "renderers" / "next-runtime-app" / "verification.json",
    },
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _metric_map(verification: dict) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for check in verification.get("checks", []):
        name = check.get("name")
        score = check.get("score")
        if isinstance(name, str) and isinstance(score, (int, float)):
            metrics[name] = float(score)
    return metrics


def _build_manifest() -> dict:
    manifest = {"samples": []}
    assets_dir = OUT_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        capture = _load_json(sample["source_capture"])
        summary = _load_json(sample["clone_summary"])
        verification = _load_json(sample["clone_verification"])

        source_image = Path(capture["bundle"]["captured_artifacts"]["screenshot"]["path"])
        rendered_capture_manifest = Path(summary["rendered_capture_manifest"])
        rendered_capture = _load_json(rendered_capture_manifest)
        clone_image = Path(rendered_capture["bundle"]["captured_artifacts"]["screenshot"]["path"])

        source_copy = assets_dir / f"{sample['id']}-source.png"
        clone_copy = assets_dir / f"{sample['id']}-clone.png"
        _copy(source_image, source_copy)
        _copy(clone_image, clone_copy)

        metrics = _metric_map(verification)
        manifest["samples"].append(
            {
                "id": sample["id"],
                "label": sample["label"],
                "score": summary.get("score"),
                "verdict": summary.get("root_report", {}).get("verdict"),
                "source_image": f"./assets/{source_copy.name}",
                "clone_image": f"./assets/{clone_copy.name}",
                "metrics": {
                    "screenshot": metrics.get("screenshot"),
                    "dom snapshot": metrics.get("dom snapshot"),
                    "computed styles": metrics.get("computed styles"),
                    "interaction states": metrics.get("interaction states"),
                    "interaction trace": metrics.get("interaction trace"),
                },
            }
        )
    return manifest


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
        <div class="subtitle">Source screenshot vs generated bounded clone runtime. Slides rotate automatically to show both a strong sample and a weaker longform sample.</div>
      </div>
      <div class="score-pill">Universal engine: 87/100</div>
    </header>
    <main id="samples"></main>
    <footer>
      <div>April 17, 2026 benchmark snapshot</div>
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
            <div class="subtitle">Self-verify score ${{sample.score}} · verdict ${{sample.verdict}}</div>
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
    setInterval(() => {{
      active = (active + 1) % manifest.samples.length;
      show(active);
    }}, 3500);
  </script>
</body>
</html>
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest()
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    html = HTML_TEMPLATE.replace("__MANIFEST__", json.dumps(manifest))
    (OUT_DIR / "index.html").write_text(html)
    print(OUT_DIR / "index.html")


if __name__ == "__main__":
    main()
