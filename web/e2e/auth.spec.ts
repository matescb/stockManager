import { randomUUID } from "node:crypto";
import type { Page, TestInfo } from "@playwright/test";

import { test, expect, DEFAULT_PASSWORD, seedPart } from "./fixtures";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const LOGIN_FAILED = "Login failed. Check your credentials and try again.";
const PASSWORD_BLOCKLIST_ERROR = "Password is too common. Choose a more unique password.";

function uniqueEmail(testInfo: TestInfo, label: string): string {
  const safeId = testInfo.testId.replace(/[^a-zA-Z0-9-]/g, "-").slice(0, 48);
  return `e2e-${safeId}-${label}-${randomUUID().slice(0, 8)}@x.com`;
}

async function login(page: Page, email: string, password = DEFAULT_PASSWORD) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function clearSession(page: Page) {
  await page.context().clearCookies();
  const response = await page.request.get("/api/auth/me");
  expect(response.status()).toBe(401);
}

async function signup(page: Page, testInfo: TestInfo, label: string) {
  const email = uniqueEmail(testInfo, label);
  await page.goto("/signup");
  await page.getByLabel("Name", { exact: true }).fill(`E2E ${label}`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password (min 8)").fill(DEFAULT_PASSWORD);
  await page.getByLabel("Workspace name (optional)").fill(`E2E ${label} workspace`);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/parts$/);
  return email;
}

async function routerFromState(page: Page) {
  return page.evaluate(() => {
    const state = window.history.state as {
      usr?: {
        from?: { pathname?: string; search?: string; hash?: string };
      };
    } | null;
    return state?.usr?.from ?? null;
  });
}

test(
  "login: existing user can log in and lands on /parts",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, email } = authedPage;
    await clearSession(page);

    await page.goto("/login");
    await login(page, email);

    await expect(page).toHaveURL(/\/parts$/);
    await expect(page.getByRole("link", { name: "Parts" }).first()).toBeVisible();
  },
);

test(
  "login: wrong password shows error inline, stays on /login",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, email } = authedPage;
    await clearSession(page);

    await page.goto("/login");
    await login(page, email, "WrongPass!!X");

    await expect(page.getByText(LOGIN_FAILED)).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  },
);

test(
  "logout: clears session, navigating to /parts redirects to /login",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    await page.goto("/parts");
    await expect(page).toHaveURL(/\/parts$/);

    await page.getByRole("button", { name: /^e2e$/ }).click();
    await page.getByRole("menuitem", { name: "Log out" }).click();

    await expect(page).toHaveURL(/\/login$/);
    const meResponse = await page.request.get("/api/auth/me");
    expect(meResponse.status()).toBe(401);

    await page.goto("/parts");
    await expect(page).toHaveURL(/\/login$/);
  },
);

test(
  "session-expiry: protected nav when unauthed redirects to /login with from-state preserved",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    const targetPath = "/parts/session-expired-sentinel/info";
    const targetSearch = "?tab=specs";
    const targetHash = "#anchor";

    await page.goto("/parts");
    await expect(page).toHaveURL(/\/parts$/);

    await page.context().clearCookies();
    await page.goto(`${targetPath}${targetSearch}${targetHash}`);

    await expect(page).toHaveURL(/\/login$/);
    await expect.poll(() => routerFromState(page)).toMatchObject({
      pathname: targetPath,
      search: targetSearch,
      hash: targetHash,
    });
  },
);

test(
  "login: after redirect-to-login, successful login restores original from URL",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request, email } = authedPage;
    const part = await seedPart(request, { name: "E2E Auth Deep Link Part" });
    const target = `/parts/${part.id}/info?panel=specs#deep-link`;

    await clearSession(page);
    await page.goto(target);

    await expect(page).toHaveURL(/\/login$/);
    await login(page, email);

    await expect.poll(() => {
      const url = new URL(page.url());
      return `${url.pathname}${url.search}${url.hash}`;
    }).toBe(target);
    await expect(page.getByText(part.name).first()).toBeVisible();
  },
);

test(
  "signup validation: invalid email and weak password render inline errors and stay on /signup",
  { tag: ["@core"] },
  async ({ page }, testInfo) => {
    await page.goto("/signup");
    await page.getByLabel("Name", { exact: true }).fill("E2E Signup Validation");
    await page.getByLabel("Email").fill("not-an-email");
    await page.getByLabel("Password (min 8)").fill(DEFAULT_PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    const emailInput = page.getByLabel("Email");
    await expect.poll(() =>
      emailInput.evaluate((input) => (input as HTMLInputElement).validity.typeMismatch),
    ).toBe(true);
    await expect(page).toHaveURL(/\/signup$/);

    const passwordInput = page.getByLabel("Password (min 8)");
    await page.getByLabel("Email").fill(uniqueEmail(testInfo, "short-password"));
    await passwordInput.fill("short");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect.poll(() =>
      passwordInput.evaluate((input) => (input as HTMLInputElement).validity.tooShort),
    ).toBe(true);
    await expect(page).toHaveURL(/\/signup$/);

    await page.getByLabel("Email").fill(uniqueEmail(testInfo, "weak-password"));
    await passwordInput.fill("password123");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText(PASSWORD_BLOCKLIST_ERROR)).toBeVisible();
    await expect(page).toHaveURL(/\/signup$/);
  },
);

test(
  "cross-workspace 404: user from ws B navigating to a part-id from ws A is not given access",
  { tag: ["@core"] },
  async ({ browser }, testInfo) => {
    const ctxA = await browser.newContext({ baseURL: BASE_URL });
    const ctxB = await browser.newContext({ baseURL: BASE_URL });

    try {
      const pageA = await ctxA.newPage();
      await signup(pageA, testInfo, "workspace-a");
      const partName = `E2E Foreign Part ${randomUUID().slice(0, 8)}`;
      const part = await seedPart(pageA.request, { name: partName });

      const pageB = await ctxB.newPage();
      await signup(pageB, testInfo, "workspace-b");
      await pageB.goto(`/parts/${part.id}/info`);

      await expect(async () => {
        const bodyText = (await pageB.locator("body").textContent()) ?? "";
        const pathname = new URL(pageB.url()).pathname;
        expect(
          pathname === "/parts" || /Failed to load part\. Not found\.|Not found/i.test(bodyText),
        ).toBe(true);
      }).toPass({ timeout: 10_000 });

      expect(await pageB.content()).not.toContain(partName);
    } finally {
      await ctxB.close();
      await ctxA.close();
    }
  },
);
