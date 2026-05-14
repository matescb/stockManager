import { test, expect } from "@playwright/test";

/**
 * Prod compose smoke (AUD-049 / @prod-smoke).
 *
 * Runs against docker-compose.prod.yml through the web container's nginx port.
 * Keep this unauthenticated: APP_ENV=prod correctly marks session cookies as
 * Secure, and CI reaches the stack over plain HTTP loopback.
 */
test("@prod-smoke SPA shell and proxied health endpoint are reachable", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();

  const health = await page.evaluate(async () => {
    const res = await fetch("/api/health");
    const body = await res.json();
    return { ok: res.ok, status: res.status, body };
  });

  expect(health).toMatchObject({
    ok: true,
    status: 200,
    body: {
      data: { status: "ok", db: "ok", uploads: "ok" },
      status: { category: "ok" },
    },
  });
});
