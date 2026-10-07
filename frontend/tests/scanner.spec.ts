import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("mobile steps preserve imported pages and description across reload", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "Review pages", exact: true }).click();
  await expect(page.locator(".pages")).toBeHidden();
  await expect(page.locator(".document-details")).toBeHidden();
  await page
    .getByRole("button", { name: "Continue to save", exact: true })
    .click();
  const note = `Imported receipt ${test.info().project.name}`;
  await page.getByLabel("Description", { exact: true }).fill(note);
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/v1/drafts")).json()).some(
          (draft: any) => draft.note === note,
        ),
      { timeout: 20000 },
    )
    .toBe(true);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: note, exact: true }),
  ).toBeVisible();
});

test("capture, adjust, reorder, download to History and export again", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles([
      resolve("../tests/fixtures/receipt.png"),
      resolve("../tests/fixtures/landscape.png"),
    ]);
  await expect(page.getByRole("button", { name: /^Page 2/ })).toBeVisible();
  await page
    .getByRole("button", { name: "Move page 2 up", exact: true })
    .click();
  await page.getByRole("button", { name: "Review pages", exact: true }).click();
  await page.getByRole("slider", { name: "Corner 1", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByAltText("Corrected document preview")).toBeVisible();
  await page.getByRole("button", { name: "Adjust", exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "Corner 1", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Continue to save", exact: true })
    .click();
  await page
    .getByLabel("Description", { exact: true })
    .fill(`Office supplies ${test.info().project.name}`);
  await page.getByLabel("Document date", { exact: true }).fill("2026-10-06");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Create PDF", exact: true }).click();
  expect((await download).suggestedFilename()).toBe(
    `2026-10-06 Office supplies ${test.info().project.name}.pdf`,
  );
  await expect(
    page.getByRole("button", { name: "New document", exact: true }),
  ).toBeVisible();
  const item = page
    .locator(".history-document")
    .filter({ hasText: `Office supplies ${test.info().project.name}` });
  await expect(item).toBeVisible();
  await expect(
    page
      .locator(".draft-card")
      .filter({ hasText: `Office supplies ${test.info().project.name}` }),
  ).toHaveCount(0);
  await item.getByRole("button", { name: "Export again" }).click();
  await expect(page.getByLabel("Description", { exact: true })).toHaveValue(
    `Office supplies ${test.info().project.name}`,
  );
  await page.getByRole("button", { name: "1 · Pages", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Page 2/ })).toBeVisible();
  await page.screenshot({
    path: `test-results/${test.info().project.name}-workspace.png`,
    fullPage: true,
  });
});

test("installed shell and edited draft survive offline reload", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  await page
    .getByLabel("Description", { exact: true })
    .fill(`Offline receipt ${test.info().project.name}`);
  await page.getByLabel("Document date", { exact: true }).fill("2026-10-06");
  await page.waitForTimeout(1800);
  await page.getByRole("button", { name: "2 · Review", exact: true }).click();
  await context.setOffline(true);
  await page.getByRole("slider", { name: "Corner 1", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await page.reload();
  await page
    .locator("article")
    .filter({
      has: page.getByRole("heading", {
        name: `Offline receipt ${test.info().project.name}`,
        exact: true,
      }),
    })
    .getByRole("button", { name: "Continue", exact: false })
    .click();
  await page
    .getByRole("button", { name: "Continue to save", exact: true })
    .click();
  await expect(page.getByLabel("Description", { exact: true })).toHaveValue(
    `Offline receipt ${test.info().project.name}`,
  );
  await expect(
    page.getByRole("button", { name: "Create PDF", exact: true }),
  ).toBeDisabled();
  await context.setOffline(false);
  await expect(
    page.getByRole("button", { name: "Create PDF", exact: true }),
  ).toBeEnabled();
});

test("administrator can configure a destination without showing its secret", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Destination settings", exact: true })
    .click();
  await page.getByLabel("Identifier", { exact: true }).fill("local-export");
  await page.getByLabel("Display name", { exact: true }).fill("Local export");
  await page
    .getByLabel("Absolute folder path", { exact: true })
    .fill("/tmp/scandoc-browser-output");
  await page
    .getByRole("button", { name: "Save destination", exact: true })
    .click();
  await expect(
    page.getByText("Destination saved", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Close settings", exact: true })
    .click();
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Destination", exact: true }),
  ).toContainText("Local export");
});

test("pointer corners align with an undistorted original and show a magnified view", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "Review pages", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Preview", exact: true }),
  ).toBeEnabled();
  const geometry = await page.locator(".corner-editor").evaluate((element) => {
    const svg = element.querySelector("svg")!;
    const image = element.querySelector("img")!;
    return {
      scale: svg.getScreenCTM()!.a,
      ratio: image.clientWidth / image.clientHeight,
      original: svg.viewBox.baseVal.width / svg.viewBox.baseVal.height,
    };
  });
  expect(geometry.ratio).toBeCloseTo(geometry.original, 2);
  const corner = page.getByRole("slider", { name: "Corner 1", exact: true });
  const before = (await corner.getAttribute("aria-valuetext"))!
    .split(",")
    .map(Number);
  const bounds = (await corner.boundingBox())!;
  const x = bounds.x + bounds.width / 2,
    y = bounds.y + bounds.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await expect(page.locator(".magnifier")).toBeVisible();
  await page.mouse.move(x + 20, y + 15, { steps: 5 });
  await page.mouse.up();
  const after = (await corner.getAttribute("aria-valuetext"))!
    .split(",")
    .map(Number);
  expect(Math.abs(after[0] - before[0] - 20 / geometry.scale)).toBeLessThan(2);
  expect(Math.abs(after[1] - before[1] - 15 / geometry.scale)).toBeLessThan(2);
});

test("home is compact and page capture controls appear once", async ({
  page,
}) => {
  await page.route("**/api/v1/drafts", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: [] })
      : route.continue(),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Documents", exact: true }),
  ).toHaveCount(0);
  const drafts = page.locator("details.drafts");
  const history = page.locator("details.history");
  await expect(drafts).not.toHaveAttribute("open", "");
  await expect(history).not.toHaveAttribute("open", "");
  await expect(drafts.locator("summary")).toContainText("Drafts");
  await expect(history.locator("summary")).toHaveText("History");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Take photo", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Add images", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Choose images", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "← All documents", exact: true })
    .click();
  await expect(drafts).toHaveAttribute("open", "");
});

test("direct export requires date and description and previews a safe filename", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  await expect(page.getByLabel("Document type", { exact: true })).toHaveCount(
    0,
  );
  const save = page.getByRole("button", { name: "Create PDF", exact: true });
  await expect(save).toBeDisabled();
  await page
    .getByLabel("Description", { exact: true })
    .fill("Cables / Workshop");
  await expect(save).toBeDisabled();
  await page.getByLabel("Document date", { exact: true }).fill("2026-10-06");
  await expect(save).toBeEnabled();
  await expect(page.locator(".export-filename")).toHaveText(
    "2026-10-06 Cables - Workshop.pdf",
  );
  await page.getByText("More options", { exact: true }).click();
  for (const name of ["PDF filename", "Correspondent", "Tags"])
    await expect(page.getByLabel(name, { exact: true })).toHaveCount(0);
});

test("History deletion removes Scandoc files and Delete all preserves unfinished drafts", async ({
  page,
}) => {
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  for (const description of ["History delete one", "History delete all"]) {
    await page
      .getByRole("button", { name: "New document", exact: true })
      .click();
    await page
      .locator("input[type=file]")
      .first()
      .setInputFiles(resolve("../tests/fixtures/receipt.png"));
    await page.getByRole("button", { name: "3 · Save", exact: true }).click();
    await page
      .getByLabel("Description", { exact: true })
      .fill(`${description} ${test.info().project.name}`);
    await page.getByLabel("Document date", { exact: true }).fill("2026-10-06");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Create PDF", exact: true }).click();
    await download;
    await expect(
      page.getByRole("button", { name: "New document", exact: true }),
    ).toBeVisible();
  }
  const original = await (await page.request.get("/api/v1/history")).json();
  const one = original.find((item: any) =>
    item.description.includes(`History delete one ${test.info().project.name}`),
  );
  await page
    .locator(".history-document")
    .filter({ hasText: `History delete one ${test.info().project.name}` })
    .getByRole("button", { name: "Delete history item" })
    .click();
  await expect
    .poll(async () =>
      (await page.request.get(`/api/v1/drafts/${one.id}`)).status(),
    )
    .toBe(404);
  expect((await page.request.get(one.jobs[0].download)).status()).toBe(404);
  await page.getByRole("button", { name: "New document", exact: true }).click();
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles(resolve("../tests/fixtures/receipt.png"));
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  await page
    .getByLabel("Description", { exact: true })
    .fill(`Unfinished keep ${test.info().project.name}`);
  await expect
    .poll(async () =>
      (await (await page.request.get("/api/v1/drafts")).json()).some(
        (d: any) => d.note === `Unfinished keep ${test.info().project.name}`,
      ),
    )
    .toBe(true);
  await page
    .getByRole("button", { name: "← All documents", exact: true })
    .click();
  await page.getByRole("button", { name: "Delete all", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/v1/history")).json()).length,
    )
    .toBe(0);
  await expect(
    page.getByRole("heading", {
      name: `Unfinished keep ${test.info().project.name}`,
      exact: true,
    }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: `Unfinished keep ${test.info().project.name}`,
      exact: true,
    }),
  ).toBeVisible();
});
