import { expect, test } from "@playwright/test";


async function expectNoHorizontalOverflow(page: import("@playwright/test").Page) {
  await expect.poll(async () => page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))).toEqual(expect.objectContaining({
    clientWidth: expect.any(Number),
    scrollWidth: expect.any(Number),
  }));
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}


test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Import without an account" })).toBeVisible({ timeout: 30_000 });
});


test("anonymous settings fit the viewport and expose import controls", async ({ page }) => {
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "Upload Listening Data" }).click();
  await expect(page.getByRole("heading", { name: "Choose music service" })).toBeVisible();
  await expect(page.getByText("Upload Takeout File")).toBeVisible();
  await expect(page.getByText("Upload Spotify Export")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});


test("navigation remains reachable at every supported viewport", async ({ page }) => {
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
    await page.getByRole("button", { name: "Close navigation", exact: true }).click();
  } else {
    await expect(page.getByRole("navigation").first()).toBeVisible();
  }
  await expectNoHorizontalOverflow(page);
});
