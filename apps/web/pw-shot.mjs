import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.goto("http://localhost:3010", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.getByRole("button", { name: /PrEP eligibility/i }).click();
await page.waitForTimeout(300);
await page.getByRole("button", { name: "Review evidence" }).click();
await page.waitForTimeout(15000);

await page.screenshot({ path: "./shot-full.png", fullPage: false });

const strip = page.locator('section[aria-label="Review provenance"]');
await strip.screenshot({ path: "./shot-strip.png" });

const inspectBtn = page.getByRole("button", { name: /Inspect evidence/i }).first();
console.log("inspect evidence count:", await inspectBtn.count());
if (await inspectBtn.count()) {
  await inspectBtn.click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "./shot-after-inspect.png" });
}

await browser.close();
