import { test, expect } from "./fixtures";

test(
  "password reset: request and complete flow renders generic states",
  { tag: ["@core"] },
  async ({ page }) => {
    await page.route("**/api/auth/request-password-reset", async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          data: { status: "accepted" },
          status: { category: "ok", message: "password reset request accepted" },
        }),
      });
    });
    await page.route("**/api/auth/reset-password", async (route) => {
      const body = route.request().postDataJSON() as {
        token: string;
        new_password: string;
      };
      expect(body.token).toBe("e2e-token");
      expect(body.new_password).toBe("NewResetPass-2026!!");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: { status: "password_reset" },
          status: { category: "ok", message: "password reset" },
        }),
      });
    });

    await page.goto("/login");
    await page.getByRole("link", { name: "Forgot password?" }).click();
    await expect(page).toHaveURL(/\/auth\/request-reset$/);

    await page.getByLabel("Email").fill("reset@example.com");
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByText(/If an account exists for reset@example.com/i)).toBeVisible();

    await page.goto("/auth/reset-password?token=e2e-token");
    await page.getByLabel("New password").fill("NewResetPass-2026!!");
    await page.getByRole("button", { name: "Save new password" }).click();
    await expect(page.getByText("Your password has been updated.")).toBeVisible();
  },
);
