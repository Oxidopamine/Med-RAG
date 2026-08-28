import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1400 } });

await page.goto("http://localhost:3010", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.getByRole("button", { name: /PrEP eligibility/i }).click();
await page.waitForTimeout(300);
await page.getByRole("button", { name: "Review evidence" }).click();
await page.waitForTimeout(15000);

const col = page.locator('.source-column, [class*="source-column"]').first();
await col.screenshot({ path: "./shot-col.png" }).catch(async () => {
  await page.screenshot({ path: "./shot-col-fallback.png", fullPage: true });
});

await browser.close();
