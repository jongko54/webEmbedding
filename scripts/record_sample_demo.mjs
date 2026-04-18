import path from "node:path";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const rootDir = process.argv[2];
const outputDir = process.argv[3];
const durationMs = Number(process.argv[4] || "9000");

if (!rootDir || !outputDir) {
  console.error("usage: node scripts/record_sample_demo.mjs <rootDir> <outputDir> [durationMs]");
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
const url = pathToFileURL(path.resolve(rootDir, "index.html")).href;
await page.goto(url, { waitUntil: "load" });
await page.waitForTimeout(durationMs);
await context.close();
await browser.close();
