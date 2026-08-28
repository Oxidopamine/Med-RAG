import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

// 1. The endpoint, loaded the way a person would load it.
const direct = await page.goto(
  "http://localhost:8010/v1/sources/WHO_HIV_DAK_2_MAIN/pages/14",
  { waitUntil: "load" },
);
console.log("DIRECT status:", direct?.status(), direct?.headers()["content-type"]);
await page.screenshot({ path: "./pdf-direct.png" });

// 2. The workspace, asking a question whose evidence is anchored in the PDF.
const failures = [];
page.on("requestfailed", (r) => failures.push(`${r.url()} :: ${r.failure()?.errorText}`));
const pageRequests = [];
page.on("response", (r) => {
  if (r.url().includes("/v1/sources/")) pageRequests.push(`${r.status()} ${r.url()}`);
});

await page.goto("http://localhost:3010", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
await page.getByRole("button", { name: /Viral load monitoring/i }).click();
await page.waitForTimeout(300);
await page.getByRole("button", { name: "Review evidence" }).click();
await page.waitForTimeout(40000);

const inspect = page.getByRole("button", { name: /Inspect evidence/i }).first();
console.log("inspect buttons:", await inspect.count());
if (await inspect.count()) {
  await inspect.click();
  await page.waitForTimeout(2500);
}
await page.screenshot({ path: "./pdf-workspace.png", fullPage: false });

console.log("source page requests:", pageRequests.length ? pageRequests : "NONE");
console.log("img elements:", await page.locator("figure img").count());
const note = await page.locator("text=/no page size to place it against/").count();
console.log("unplaceable note present:", note);
const licence = await page.locator("text=/Licence does not permit/").count();
console.log("licence notice present:", licence);
if (failures.length) console.log("request failures:", failures.slice(0, 5));

await browser.close();
