import { randomUUID } from "node:crypto";

import {
  DEFAULT_PASSWORD,
  EMAIL_DOMAIN,
  expect,
  PartDetailPage,
  seedPart,
  seedStorage,
  test,
  type AuthedPage,
} from "./fixtures";

type Page = AuthedPage["page"];

type SignupEnvelope = {
  data: {
    user: { id: string; email: string; name: string };
    workspace_id: string;
  };
  status: { category: string; message: string };
};

function uniqueName(prefix: string): string {
  return `${prefix} ${randomUUID().slice(0, 8)}`;
}

async function createLocalPart(page: Page, name: string, mpn?: string): Promise<void> {
  await page.goto("/parts/create");
  await page.getByLabel(/^type$/i).selectOption("local");
  await page.getByLabel(/^name$/i).fill(name);
  if (mpn) {
    await page.getByLabel(/mpn/i).fill(mpn);
  }
  await page.getByRole("button", { name: /^create$/i }).click();
}

async function signupNewWorkspace(page: Page): Promise<SignupEnvelope["data"]> {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  const email = `e2e-cross-${randomUUID().slice(0, 10)}@${EMAIL_DOMAIN}`;
  const response = await page.request.post("/api/auth/signup", {
    data: {
      email,
      name: "e2e cross workspace",
      password: DEFAULT_PASSWORD,
    },
  });
  expect(response.ok()).toBe(true);
  const envelope = (await response.json()) as SignupEnvelope;
  expect(envelope).toHaveProperty("data");
  expect(envelope).toHaveProperty("status");
  return envelope.data;
}

test(
  'empty parts list shows "+ Part" CTA on fresh workspace',
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    await page.goto("/parts");

    await expect(page.getByText("No parts yet")).toBeVisible();
    const emptyStateCell = page.locator("td", { hasText: "No parts yet" });
    const emptyStateCta = emptyStateCell.getByRole("link", { name: /\+ part/i });
    await expect(emptyStateCta).toHaveCount(1);
    await expect(emptyStateCta).toBeVisible();
    await expect(emptyStateCta).toHaveAttribute("href", "/parts/create");
  },
);

test(
  '"+ Part" link routes to /parts/create and renders form',
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    await page.goto("/parts");

    const emptyStateCell = page.locator("td", { hasText: "No parts yet" });
    await emptyStateCell.getByRole("link", { name: /\+ part/i }).click();

    await page.waitForURL(/\/parts\/create$/);
    await expect(page.getByLabel(/^name$/i)).toBeVisible();
    await expect(page.getByLabel(/mpn/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /^create$/i })).toBeEnabled();
  },
);

test(
  "created part appears in list with on_hand=0",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    const name = uniqueName("E2E Part List");

    await createLocalPart(page, name);
    await page.waitForURL(/\/parts\/[0-9a-f-]+\/info$/);
    await page.goto("/parts");

    const row = page.getByRole("row").filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row.locator("td").last()).toHaveText("0");
  },
);

test(
  "row click navigates to /info tab",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const name = uniqueName("E2E Row Click");
    await seedPart(request, { name });

    await page.goto("/parts");
    const row = page.getByRole("row").filter({ hasText: name });
    await row.getByText(name).click();

    await page.waitForURL(/\/parts\/[0-9a-f-]+\/info$/);
    await expect(page.getByText(name).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Part info", exact: true })).toHaveClass(/bg-panel/);
  },
);

test(
  'name-only (no MPN) creates a "local" part',
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    const name = uniqueName("E2E Local Only");

    await createLocalPart(page, name);

    await page.waitForURL(/\/parts\/[0-9a-f-]+\/info$/);
    await expect(page.getByText(name).first()).toBeVisible();
    await expect(page.locator(".pill").filter({ hasText: "local" })).toBeVisible();
  },
);

test(
  "MPN collision returns 409 and renders mpn-conflict-banner with link to existing part",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const originalName = uniqueName("Original Part");
    const original = await seedPart(request, {
      name: originalName,
      mpn: "STM32F103C8T6",
    });

    await page.goto("/parts/create");
    await page.getByLabel(/^name$/i).fill("Dupe Attempt");
    await page.getByLabel(/mpn/i).fill("STM32F103C8T6");
    await page.getByRole("button", { name: /^create$/i }).click();

    const banner = page.getByTestId("mpn-conflict-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(originalName);
    await expect(banner.getByRole("link", { name: /open existing part/i }))
      .toHaveAttribute("href", `/parts/${original.id}/info`);
    await expect(page).toHaveURL(/\/parts\/create$/);
  },
);

test(
  "archived part's MPN is reusable — no conflict banner",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    await seedPart(request, {
      name: uniqueName("Archived One"),
      mpn: "STM32F103C8T6",
      archived: true,
    });

    await page.goto("/parts/create");
    await page.getByLabel(/^name$/i).fill("Reused MPN");
    await page.getByLabel(/mpn/i).fill("STM32F103C8T6");
    await page.getByRole("button", { name: /^create$/i }).click();

    await page.waitForURL(/\/parts\/[0-9a-f-]+\/info$/);
    await expect(page.getByTestId("mpn-conflict-banner")).toHaveCount(0);
    await expect(page.getByText("Reused MPN").first()).toBeVisible();
  },
);

test(
  "cross-workspace navigation returns 404 / redirects",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const name = uniqueName("E2E Workspace A Part");
    const partA = await seedPart(request, { name });

    await signupNewWorkspace(page);
    await page.goto(`/parts/${partA.id}/info`);

    const notFound = page.getByText("Failed to load part. Not found.");
    const emptyParts = page.getByText("No parts yet");
    await expect(notFound.or(emptyParts)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(name)).toHaveCount(0);
  },
);

test(
  "tab nav (info → specs → stock → history) does not full-reload",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const storage = await seedStorage(request, { name: uniqueName("E2E Tab Bin") });
    const part = await seedPart(request, {
      name: uniqueName("E2E Tab Part"),
      initial_qty: 5,
      storage_location_id: storage.id,
    });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("info");
    const navigationStart = await page.evaluate(() => performance.timing.navigationStart);

    await detail.openTab("Specs");
    await expect(page).toHaveURL(new RegExp(`/parts/${part.id}/specs$`));
    expect(await page.evaluate(() => performance.timing.navigationStart)).toBe(navigationStart);

    await detail.openTab("Stock");
    await expect(page).toHaveURL(new RegExp(`/parts/${part.id}/stock$`));
    expect(await page.evaluate(() => performance.timing.navigationStart)).toBe(navigationStart);
    await expect(detail.onHandValue).toHaveText("5");

    await detail.openTab("History");
    await expect(page).toHaveURL(new RegExp(`/parts/${part.id}/history$`));
    expect(await page.evaluate(() => performance.timing.navigationStart)).toBe(navigationStart);
  },
);

test(
  "Settings save round-trips low_stock_report_quantity",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const part = await seedPart(request, { name: uniqueName("E2E Threshold Part") });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("settings");
    await page.getByLabel(/low[- ]stock/i).fill("7");
    const saved = page.waitForResponse((response) =>
      response.url().includes(`/api/parts/${part.id}`) &&
      response.request().method() === "PATCH" &&
      response.ok()
    );
    await page.getByRole("button", { name: /^save$/i }).click();
    await saved;

    await page.goto("/parts");
    await detail.goto("info");
    const threshold = page.getByText("Threshold", { exact: true });
    await expect(threshold).toBeVisible();
    await expect(threshold.locator("xpath=..").getByText("7", { exact: true })).toBeVisible();
  },
);
