import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const credentials = Object.fromEntries(
  readFileSync(resolve("../../.env"), "utf8")
    .split("\n")
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => {
      const index = line.indexOf("=");
      return [line.slice(0, index), line.slice(index + 1)];
    }),
);
async function login(page: any, user = "scanner-team") {
  await page.goto("/");
  await page.getByLabel("Username", { exact: true }).fill(user);
  await page
    .getByLabel("Password", { exact: true })
    .fill(
      credentials[
        user === "admin" ? "TEST_ADMIN_PASSWORD" : "TEST_TEAM_PASSWORD"
      ],
    );
  await page.getByRole("button", { name: "Sign in", exact: false }).click();
  await expect(
    page.getByRole("button", { name: "New document", exact: true }),
  ).toBeVisible();
}
async function configure(page: any) {
  const identity = await (await page.request.get("/api/v1/session")).json();
  for (const [id, value] of Object.entries({
    "browser-folder": {
      kind: "folder",
      name: "Browser folder",
      root: "/app/output",
    },
    "browser-cloud": {
      kind: "webdav",
      name: "Browser cloud",
      url: "https://gateway/cloud/remote.php/dav/files/archive",
      username: "archive",
      password: credentials.TEST_WEBDAV_PASSWORD,
    },
    "browser-unavailable": {
      kind: "webdav",
      name: "Unavailable cloud",
      url: "https://unavailable.invalid/collection",
      username: "archive",
      password: "disposable",
    },
  })) {
    const result = await page.request.put("/api/v1/admin/destinations/" + id, {
      data: value,
      headers: {
        "X-CSRF-Token": identity.csrf,
        Origin: process.env.SCANDOC_TEST_URL!,
      },
    });
    expect(result.ok()).toBeTruthy();
  }
  await page.reload();
}
for (const destination of [
  "download",
  "browser-folder",
  "browser-cloud",
  "paperless",
]) {
  test(`hosted upload, corners, reorder, preview and ${destination}`, async ({
    page,
  }) => {
    test.setTimeout(240000);
    await login(page, "admin");
    await configure(page);
    await page
      .getByRole("button", { name: "New document", exact: true })
      .click();
    await page
      .locator("input[type=file]")
      .first()
      .setInputFiles([
        resolve("../tests/fixtures/receipt.png"),
        resolve("../tests/fixtures/landscape.png"),
      ]);
    await expect(page.getByRole("button", { name: /^Page 2/ })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Preview", exact: true }),
    ).toBeEnabled();
    await page.getByRole("slider", { name: "Corner 1", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    await page
      .getByRole("button", { name: "Move page 2 up", exact: true })
      .click();
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByAltText("Corrected document preview")).toBeVisible();
    await page
      .getByLabel("Note", { exact: true })
      .fill(`Hosted ${destination} ${test.info().project.name} ${Date.now()}`);
    await page
      .getByRole("combobox", { name: "Destination", exact: true })
      .selectOption(destination);
    if (destination === "paperless")
      await page
        .getByRole("combobox", { name: "Document type", exact: true })
        .selectOption({ label: "Receipt" });
    const created = page.waitForResponse(
      (r: any) => r.url().endsWith("/jobs") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: destination === "download" ? "Create PDF" : "Save document",
        exact: true,
      })
      .click();
    const job = await (await created).json();
    expect(job.id).toBeTruthy();
    await expect
      .poll(
        async () =>
          (await (await page.request.get("/api/v1/jobs/" + job.id)).json())
            .status,
        { timeout: 200000, intervals: [1000] },
      )
      .toBe(destination === "download" ? "ready" : "delivered");
    if (destination === "download") {
      await expect(
        page.getByRole("link", { name: "Download PDF" }),
      ).toBeVisible();
      const event = page.waitForEvent("download");
      await page.getByRole("link", { name: "Download PDF" }).click();
      expect((await event).suggestedFilename()).toBe("document.pdf");
    } else
      await expect(
        page.getByRole("heading", { name: "Documents", exact: true }),
      ).toBeVisible({ timeout: 10000 });
  });
}
test("logout hides retained drafts; another account cannot read them; same account restores them", async ({
  page,
  context,
}) => {
  await login(page);
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  const name = `Private draft ${test.info().project.name} ${Date.now()}`;
  await page.getByLabel("Note", { exact: true }).fill(name);
  await expect
    .poll(
      async () => {
        const synced = await (await page.request.get("/api/v1/drafts")).json();
        return synced.some((d: any) => d.note === name);
      },
      { timeout: 20000 },
    )
    .toBe(true);
  const drafts = await (await page.request.get("/api/v1/drafts")).json();
  const draft = drafts.find((d: any) => d.note === name);
  expect(draft).toBeTruthy();
  await page.evaluate(() => navigator.serviceWorker.ready);
  // A disconnected logout must hide drafts before the server cookie is revoked.
  await context.setOffline(true);
  await page.getByRole("button", { name: /Sign out/ }).click();
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await context.setOffline(false);
  await login(page, "admin");
  await expect(page.getByRole("heading", { name, exact: true })).toHaveCount(0);
  expect((await page.request.get("/api/v1/drafts/" + draft.id)).status()).toBe(
    404,
  );
  await page.getByRole("button", { name: /Sign out/ }).click();
  await login(page);
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
});
test("failed destination retains draft and expired session prompts login", async ({
  page,
}) => {
  await login(page, "admin");
  await configure(page);
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page
    .getByRole("combobox", { name: "Destination", exact: true })
    .selectOption("browser-unavailable");
  await page
    .getByRole("button", { name: "Save document", exact: true })
    .click();
  await expect(page.getByText("failed", { exact: true })).toBeVisible({
    timeout: 45000,
  });
  await expect(
    page.getByRole("button", { name: "Retry delivery", exact: true }),
  ).toBeVisible();
  const identity = await (await page.request.get("/api/v1/session")).json();
  const result = await page.request.delete("/api/v1/session", {
    headers: {
      "X-CSRF-Token": identity.csrf,
      Origin: process.env.SCANDOC_TEST_URL!,
    },
  });
  expect(result.ok()).toBeTruthy();
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible({
    timeout: 10000,
  });
});
