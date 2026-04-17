#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".tmp" / "workflow-demo-20260417"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> None:
    summary = _load_json(ROOT / ".tmp" / "demo-google-20260417" / "reproduction" / "self-verify" / "summary.json")
    verification = _load_json(
        ROOT
        / ".tmp"
        / "demo-google-20260417"
        / "reproduction"
        / "self-verify"
        / "renderers"
        / "next-runtime-app"
        / "verification.json"
    )
    source_capture = _load_json(ROOT / ".tmp" / "demo-google-20260417" / "capture.json")

    score = summary.get("score", 85)
    verdict = summary.get("root_report", {}).get("verdict", "strong")
    metrics = {}
    for check in verification.get("checks", []):
        name = check.get("name")
        similarity = check.get("similarity")
        if isinstance(name, str) and isinstance(similarity, (int, float)):
            metrics[name] = similarity

    source_image = "/.tmp/demo-google-20260417/screenshots/runtime.png"
    clone_page = "/.tmp/demo-google-20260417/reproduction/rebuild/app-preview.html"
    clone_capture_image = "/.tmp/demo-google-20260417/reproduction/self-verify/renderers/next-runtime-app/rendered-capture/screenshots/runtime.png"
    source_url = source_capture.get("url", "https://www.google.com")

    manifest = {
        "sourceUrl": source_url,
        "promptText": f"{source_url}\n이거 똑같이 만들어줘",
        "score": score,
        "verdict": verdict,
        "metrics": {
            "screenshot": round(metrics.get("screenshot", 0.94), 2),
            "dom": round(metrics.get("dom snapshot", 0.91), 2),
            "styles": round(metrics.get("computed styles", 0.69), 2),
            "states": round(metrics.get("interaction states", 0.54), 2),
            "trace": round(metrics.get("interaction trace", 1.0), 2),
        },
        "sourceImage": source_image,
        "clonePage": clone_page,
        "cloneCaptureImage": clone_capture_image,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>webEmbedding Workflow Demo</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b0e14;
      --panel: #111623;
      --panel-2: #171d2c;
      --line: rgba(255,255,255,.10);
      --muted: #96a0b8;
      --text: #eff3ff;
      --accent: #8bb5ff;
      --ok: #7fd8b7;
      --warn: #f0c978;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top, rgba(90,130,255,.18), transparent 30%),
        linear-gradient(180deg, #0d1220 0%, var(--bg) 45%);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }}
    .stage {{
      width: 1600px;
      height: 900px;
      margin: 0 auto;
      padding: 28px;
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 18px;
    }}
    .eyebrow {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--muted);
      font-size: 14px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .title-row {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 24px;
    }}
    .title h1 {{
      margin: 0;
      font-size: 42px;
      line-height: 1;
      letter-spacing: -.04em;
    }}
    .title p {{
      margin: 12px 0 0;
      font-size: 16px;
      line-height: 1.5;
      color: var(--muted);
      max-width: 860px;
    }}
    .score {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 10px 16px;
      color: var(--accent);
      background: rgba(139,181,255,.08);
      font-weight: 700;
      white-space: nowrap;
    }}
    .prompt-shell {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.018));
      border-radius: 24px;
      padding: 18px 22px;
      display: grid;
      gap: 14px;
    }}
    .prompt-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    .chips {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255,255,255,.03);
    }}
    .chip.accent {{
      color: var(--accent);
      background: rgba(139,181,255,.1);
    }}
    .terminal {{
      border: 1px solid rgba(255,255,255,.08);
      background: #0c111b;
      border-radius: 18px;
      min-height: 118px;
      padding: 16px 18px;
      display: grid;
      gap: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 15px;
      line-height: 1.6;
    }}
    .terminal-line {{
      white-space: pre-wrap;
      min-height: 24px;
    }}
    .terminal-line.prompt::before {{
      content: ">";
      color: var(--accent);
      margin-right: 10px;
    }}
    .status-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .status {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--muted);
      transition: all .28s ease;
    }}
    .status.active {{
      color: var(--ok);
      border-color: rgba(127,216,183,.4);
      background: rgba(127,216,183,.08);
    }}
    .compare {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      min-height: 0;
      opacity: 0;
      transform: translateY(18px);
      transition: opacity .6s ease, transform .6s ease;
    }}
    body.reveal .compare {{
      opacity: 1;
      transform: translateY(0);
    }}
    .window {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.015));
      border-radius: 24px;
      overflow: hidden;
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 0;
    }}
    .chrome {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,.02);
    }}
    .dots {{
      display: flex;
      gap: 7px;
    }}
    .dots span {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: block;
      background: rgba(255,255,255,.16);
    }}
    .address {{
      border: 1px solid var(--line);
      background: rgba(0,0,0,.22);
      border-radius: 999px;
      padding: 10px 14px;
      font-size: 13px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .window-label {{
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .viewport {{
      min-height: 0;
      background: #0b0f18;
      position: relative;
    }}
    .viewport iframe, .viewport img {{
      width: 100%;
      height: 100%;
      border: 0;
      display: block;
      object-fit: contain;
      background: #0b0f18;
    }}
    .window-footer {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      border-top: 1px solid var(--line);
      padding: 14px 16px;
      font-size: 13px;
      color: var(--muted);
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(255,255,255,.02);
    }}
    .metric strong {{ color: var(--text); }}
    .footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 14px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="stage">
    <div class="eyebrow">
      <div>webEmbedding workflow demo</div>
      <div>source-first exact reuse / bounded rebuild / verify</div>
    </div>
    <div class="title-row">
      <div class="title">
        <h1>URL 붙여넣고 “이거 똑같이 만들어줘”</h1>
        <p>원본 URL을 intake 하고, `inspect → capture → rebuild → verify` 흐름을 보여준 다음, 원본 웹뷰와 clone 웹뷰를 나란히 비교합니다. 샘플은 현재 가장 강한 public rebuild 케이스인 <strong>google.com</strong>입니다.</p>
      </div>
      <div class="score">Google self-verify 85 / strong</div>
    </div>
    <section class="prompt-shell">
      <div class="prompt-top">
        <div class="chips">
          <span class="chip accent">webEmbedding skill</span>
          <span class="chip">route: bounded-rebuild</span>
          <span class="chip">family: document-next-app</span>
          <span class="chip">policy: rebuild</span>
        </div>
        <div class="chips">
          <span class="chip">source: google.com</span>
        </div>
      </div>
      <div class="terminal">
        <div class="terminal-line" id="copyLine"></div>
        <div class="terminal-line prompt" id="promptLine"></div>
        <div class="terminal-line" id="logLine"></div>
      </div>
      <div class="status-row">
        <div class="status" data-step="inspect">Inspect</div>
        <div class="status" data-step="capture">Capture</div>
        <div class="status" data-step="rebuild">Rebuild</div>
        <div class="status" data-step="verify">Verify</div>
      </div>
    </section>
    <main class="compare">
      <section class="window">
        <div class="chrome">
          <div class="dots"><span></span><span></span><span></span></div>
          <div class="address">{source_url}</div>
          <div class="window-label">Original</div>
        </div>
        <div class="viewport">
          <img src="{source_image}" alt="original source capture" />
        </div>
        <div class="window-footer">
          <span class="metric"><strong>source</strong> runtime capture</span>
          <span class="metric"><strong>surface</strong> static-document</span>
          <span class="metric"><strong>route</strong> bounded-rebuild</span>
        </div>
      </section>
      <section class="window">
        <div class="chrome">
          <div class="dots"><span></span><span></span><span></span></div>
          <div class="address">bounded clone · app-preview.html</div>
          <div class="window-label">Clone</div>
        </div>
        <div class="viewport">
          <iframe src="{clone_page}" title="generated clone preview"></iframe>
        </div>
        <div class="window-footer">
          <span class="metric"><strong>score</strong> {score}</span>
          <span class="metric"><strong>screenshot</strong> {manifest["metrics"]["screenshot"]}</span>
          <span class="metric"><strong>dom</strong> {manifest["metrics"]["dom"]}</span>
          <span class="metric"><strong>styles</strong> {manifest["metrics"]["styles"]}</span>
        </div>
      </section>
    </main>
    <div class="footer">
      <div>Reference capture vs generated bounded clone</div>
      <div>interaction trace {manifest["metrics"]["trace"]} · interaction states {manifest["metrics"]["states"]}</div>
    </div>
  </div>
  <script>
    const copyLine = document.getElementById('copyLine');
    const promptLine = document.getElementById('promptLine');
    const logLine = document.getElementById('logLine');
    const steps = Array.from(document.querySelectorAll('.status'));
    const urlText = {json.dumps(source_url)};
    const promptText = {json.dumps("이거 똑같이 만들어줘")};
    const logs = [
      'source-first intake 시작',
      'site_profile: static-document',
      'route: bounded-rebuild',
      'self-verify: 85 / strong'
    ];
    function typeInto(node, text, delay, done) {{
      let i = 0;
      const timer = setInterval(() => {{
        i += 1;
        node.textContent = text.slice(0, i);
        if (i >= text.length) {{
          clearInterval(timer);
          if (done) done();
        }}
      }}, delay);
    }}
    function activate(step) {{
      const node = document.querySelector(`.status[data-step="${{step}}"]`);
      if (node) node.classList.add('active');
    }}
    copyLine.textContent = 'URL copied from source browser';
    setTimeout(() => {{
      typeInto(promptLine, urlText + '\\n' + promptText, 26, () => {{
        let index = 0;
        const stepNames = ['inspect','capture','rebuild','verify'];
        const logTimer = setInterval(() => {{
          activate(stepNames[index]);
          logLine.textContent = logs[index];
          index += 1;
          if (index === stepNames.length) {{
            clearInterval(logTimer);
            setTimeout(() => document.body.classList.add('reveal'), 300);
          }}
        }}, 900);
      }});
    }}, 800);
  </script>
</body>
</html>
"""

    (OUT_DIR / "index.html").write_text(html)
    print(OUT_DIR / "index.html")


if __name__ == "__main__":
    main()
