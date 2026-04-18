import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const target = process.argv[2];
const outputDir = process.argv[3];
const durationMs = Number(process.argv[4] || "12000");

if (!target || !outputDir) {
  console.error("usage: node scripts/record_workflow_demo.mjs <rootDirOrUrl> <outputDir> [durationMs]");
  process.exit(1);
}

fs.mkdirSync(outputDir, { recursive: true });

const browserCandidates = [
  process.env.WEB_EMBEDDING_CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
];
const browserPath = browserCandidates.find((candidate) => candidate && fs.existsSync(candidate));

const browser = await chromium.launch({
  headless: true,
  executablePath: browserPath || undefined,
});

const context = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  recordVideo: {
    dir: outputDir,
    size: { width: 1600, height: 900 },
  },
});

const page = await context.newPage();
const url =
  target.startsWith("http://") || target.startsWith("https://") || target.startsWith("file://")
    ? target
    : pathToFileURL(path.resolve(target, "index.html")).href;
await page.goto(url, { waitUntil: "load" });
await page.waitForTimeout(durationMs);
await context.close();
await browser.close();
