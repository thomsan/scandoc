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
  const headers = {
    "X-CSRF-Token": identity.csrf,
    Origin: process.env.SCANDOC_TEST_URL!,
  };
  for (const [id, value] of Object.entries({
    "browser-folder": {
      kind: "folder",
      name: "Browser folder",
      root: "/app/output",
    },
    "browser-unavailable": {
      kind: "folder",
      name: "Unavailable folder",
      root: "/proc/scandoc-browser-readonly",
    },
  })) {
    const result = await page.request.put("/api/v1/admin/destinations/" + id, {
      data: value,
      headers,
    });
    expect(result.ok()).toBeTruthy();
  }
  const connected = await page.request.post("/api/v1/webdav/accounts", {
    data: {
      url: "https://gateway/cloud",
      username: "scanner-admin",
      password: credentials.TEST_ADMIN_PASSWORD,
      name: "My cloud",
    },
    headers,
  });
  expect(connected.ok()).toBeTruthy();
  const account = (await connected.json()).id;
  const selected = await page.request.post(
    `/api/v1/webdav/accounts/${account}/destinations`,
    {
      data: { path: "/", name: "Browser cloud" },
      headers,
    },
  );
  expect(selected.ok()).toBeTruthy();
  const destination = (await selected.json()).id;
  await page.reload();
  return destination;
}

for (const destination of [
  "download",
  "browser-folder",
  "browser-cloud",
  "paperless",
]) {
  test(`hosted upload, corners, reorder, preview and ${destination}`, async ({
    page,
    context,
  }) => {
    test.setTimeout(240000);
    await login(page, "admin");
    const cloudDestination = await configure(page);
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
    await page
      .getByRole("button", { name: "Move page 2 up", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Review pages", exact: true })
      .click();
    await page.getByRole("slider", { name: "Corner 1", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByAltText("Corrected document preview")).toBeVisible();
    await page
      .getByRole("button", { name: "Continue to save", exact: true })
      .click();
    const description = `Hosted ${destination} ${test.info().project.name} ${Date.now()}`;
    await page.getByLabel("Description", { exact: true }).fill(description);
    await page
      .getByRole("combobox", { name: "Destination", exact: true })
      .selectOption(
        destination === "browser-cloud" ? cloudDestination : destination,
      );
    if (destination === "paperless") {
      await expect(
        page.getByLabel("Document date", { exact: true }),
      ).toHaveCount(0);
      await page
        .getByRole("combobox", { name: "Document type", exact: true })
        .selectOption({ label: "Receipt" });
    } else {
      await expect(
        page.getByLabel("Document type", { exact: true }),
      ).toHaveCount(0);
      await page
        .getByLabel("Document date", { exact: true })
        .fill("2026-10-06");
    }
    const created = page.waitForResponse(
      (r: any) => r.url().endsWith("/jobs") && r.request().method() === "POST",
    );
    const automaticDownload =
      destination === "download" ? page.waitForEvent("download") : null;
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
    if (automaticDownload)
      expect((await automaticDownload).suggestedFilename()).toBe(
        `2026-10-06 ${description}.pdf`,
      );
    await expect(
      page.getByRole("button", { name: "New document", exact: true }),
    ).toBeVisible({ timeout: 10000 });
    const retained = await (
      await page.request.get(`/api/v1/drafts/${job.draft_id}`)
    ).json();
    expect(retained.archived).toBe(true);
    expect(retained.pages).toHaveLength(2);
    const historyItem = page
      .locator(".history-document")
      .filter({ hasText: description });
    await expect(historyItem).toBeVisible();
    if (destination === "browser-cloud")
      await expect(
        historyItem.getByRole("link", { name: "Open document" }),
      ).toHaveCount(0);
    if (destination === "paperless") {
      await page.evaluate(() => navigator.serviceWorker.ready);
      const delivered = await (
        await page.request.get("/api/v1/jobs/" + job.id)
      ).json();
      await expect(page.locator("details.history")).toHaveAttribute("open", "");
      const opened = page.waitForEvent("popup");
      await page.locator(`a[href="${delivered.location}"]`).click();
      const archive = await opened;
      await archive.locator('input[name="login"]').fill("scanner-team");
      await archive
        .locator('input[name="password"]')
        .fill(credentials.TEST_TEAM_PASSWORD);
      await archive.locator('button[type="submit"]').click();
      await expect(archive).toHaveURL(delivered.location);
      await expect(archive.locator("pngx-document-detail")).toBeVisible({
        timeout: 20000,
      });
      await archive.close();
      await context.setOffline(true);
      await page.reload();
      await expect(
        page.getByRole("button", { name: "New document", exact: true }),
      ).toBeVisible();
      await context.setOffline(false);
    }
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
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  const name = `Private draft ${test.info().project.name} ${Date.now()}`;
  await page.getByLabel("Description", { exact: true }).fill(name);
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
  await page.getByRole("button", { name: "3 · Save", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Destination", exact: true })
    .selectOption("browser-unavailable");
  await page.getByLabel("Document date", { exact: true }).fill("2026-10-06");
  await page
    .getByLabel("Description", { exact: true })
    .fill("Unavailable export");
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

test("ordinary user connects personal WebDAV, browses folders, uploads and signs in again after logout", async ({
  page,
}) => {
  test.setTimeout(180000);
  await login(page);
  const folder = `Scandoc personal ${test.info().project.name} ${Date.now()}`;
  const providerRoot = "/cloud/remote.php/dav/files/scanner-team/";
  const providerHeaders = {
    Authorization:
      "Basic " +
      Buffer.from(`scanner-team:${credentials.TEST_TEAM_PASSWORD}`).toString(
        "base64",
      ),
  };
  const createdFolder = await page.request.fetch(
    providerRoot + encodeURIComponent(folder),
    { method: "MKCOL", headers: providerHeaders },
  );
  expect(createdFolder.ok()).toBeTruthy();
  try {
    await page
      .getByRole("button", { name: "Destination settings", exact: true })
      .click();
    await expect(page.getByLabel("Identifier", { exact: true })).toHaveCount(0);
    await page
      .getByLabel("Server URL", { exact: true })
      .fill("https://gateway/cloud");
    const username = page.getByLabel("WebDAV username", { exact: true });
    const password = page.getByLabel("App password", { exact: true });
    await expect(username).toHaveAttribute(
      "autocomplete",
      "section-webdav username",
    );
    await expect(password).toHaveAttribute(
      "autocomplete",
      "section-webdav current-password",
    );
    await username.fill("scanner-team");
    await password.fill(credentials.TEST_TEAM_PASSWORD);
    const connectionResponse = page.waitForResponse(
      (r) =>
        r.url().endsWith("/webdav/accounts") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Sign in to WebDAV", exact: true })
      .click();
    const connection = await (await connectionResponse).json();
    expect(connection.connected).toBe(true);
    expect(connection.password).toBeUndefined();
    await expect(
      page.getByRole("heading", { name: "Choose upload folder", exact: true }),
    ).toBeVisible();
    await page
      .locator(".folder-list")
      .getByRole("button", { name: folder, exact: true })
      .click();
    await expect(page.getByLabel("Current folder")).toHaveText(`/${folder}/`);
    await page
      .getByRole("button", { name: "Parent folder", exact: true })
      .click();
    await expect(page.getByLabel("Current folder")).toHaveText("/");
    await page
      .locator(".folder-list")
      .getByRole("button", { name: folder, exact: true })
      .click();
    const destinationName = `Personal cloud ${test.info().project.name} ${Date.now()}`;
    await page
      .getByLabel("Destination name", { exact: true })
      .fill(destinationName);
    await page.getByLabel("Use as my default destination").check();
    const selected = page.waitForResponse(
      (r) =>
        r.url().endsWith("/destinations") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Use this folder", exact: true })
      .click();
    const destination = await (await selected).json();
    await expect(
      page.getByRole("dialog", { name: "Destination settings" }),
    ).toHaveCount(0);
    await expect
      .poll(
        async () =>
          (await (await page.request.get("/api/v1/destinations")).json())[0].id,
      )
      .toBe(destination.id);
    await page
      .getByRole("button", { name: "New document", exact: true })
      .click();
    await page
      .locator("input[type=file]")
      .first()
      .setInputFiles(resolve("../tests/fixtures/receipt.png"));
    await page.getByRole("button", { name: "3 · Save", exact: true }).click();
    await expect(
      page.getByRole("combobox", { name: "Destination", exact: true }),
    ).toHaveValue(destination.id);
    const description = `Personal upload ${Date.now()}`;
    await page.getByLabel("Description", { exact: true }).fill(description);
    await page.getByLabel("Document date", { exact: true }).fill("2026-10-07");
    await page
      .getByRole("button", { name: "Save document", exact: true })
      .click();
    const history = page
      .locator(".history-document")
      .filter({ hasText: description });
    await expect(history).toBeVisible({ timeout: 30000 });
    await expect(history).toContainText("Delivered");
    const remote = await page.request.get(
      providerRoot +
        encodeURIComponent(folder) +
        "/" +
        encodeURIComponent(`2026-10-07 ${description}.pdf`),
      { headers: providerHeaders },
    );
    expect(remote.ok()).toBeTruthy();
    expect((await remote.body()).subarray(0, 4).toString()).toBe("%PDF");
    const saved = await page.evaluate(async () => {
      const db = await new Promise<IDBDatabase>((resolve) => {
        const r = indexedDB.open("scandoc");
        r.onsuccess = () => resolve(r.result);
      });
      const values = await Promise.all(
        Array.from(db.objectStoreNames).map(
          (name) =>
            new Promise((resolve) => {
              const r = db.transaction(name).objectStore(name).getAll();
              r.onsuccess = () => resolve(r.result);
            }),
        ),
      );
      db.close();
      return JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
        values,
      });
    });
    expect(saved.includes(credentials.TEST_TEAM_PASSWORD)).toBe(false);
    await page.getByRole("button", { name: /Sign out/ }).click();
    await login(page);
    const connections = await (
      await page.request.get("/api/v1/webdav/accounts")
    ).json();
    expect(connections.find((a: any) => a.id === connection.id).connected).toBe(
      false,
    );
    await page
      .getByRole("button", { name: "Destination settings", exact: true })
      .click();
    const accountCard = page
      .locator(".webdav-connection")
      .filter({ hasText: connection.url });
    await accountCard
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await accountCard
      .getByLabel("App password", { exact: true })
      .fill(credentials.TEST_TEAM_PASSWORD);
    await accountCard
      .getByRole("button", { name: "Sign in to WebDAV", exact: true })
      .click();
    await expect(accountCard).toContainText("Signed in for this session");
    await expect(
      accountCard.getByRole("button", { name: "Choose folder", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "Close settings", exact: true })
      .click();
    const session = await (await page.request.get("/api/v1/session")).json();
    const deleted = await page.request.delete(
      `/api/v1/webdav/accounts/${connection.id}`,
      {
        headers: {
          "X-CSRF-Token": session.csrf,
          Origin: process.env.SCANDOC_TEST_URL!,
        },
      },
    );
    expect(deleted.ok()).toBeTruthy();
  } finally {
    await page.request.delete(providerRoot + encodeURIComponent(folder), {
      headers: providerHeaders,
    });
  }
});
