import fs from "node:fs";
import { chromium } from "playwright-core";

const url = process.argv[2];
const outputDir = process.argv[3];
const durationMs = Number(process.argv[4] || "12000");

if (!url || !outputDir) {
  console.error("usage: node scripts/record_workflow_demo.mjs <url> <outputDir> [durationMs]");
  process.exit(1);
}

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
await page.goto(url, { waitUntil: "load" });
await page.waitForTimeout(durationMs);
await context.close();
await browser.close();
