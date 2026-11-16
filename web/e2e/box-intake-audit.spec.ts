import { readFileSync } from "node:fs";

import { expect, seedPart, seedStorage, test, type AuthedPage } from "./fixtures";

type Page = AuthedPage["page"];

const bagFixture = JSON.parse(
  readFileSync(new URL("../src/lib/__fixtures__/bagSignatures.json", import.meta.url), "utf8"),
) as { bags: Array<{ expected_mpn?: string; raws: string[] }> };

const bagsWithMpn = bagFixture.bags.filter(b => b.expected_mpn && b.raws[0]);

async function scan(page: Page, raw: string) {
  const input = page.getByLabel("Bag code");
  await input.fill(raw);
  await input.press("Enter");
}

test(
  "box intake adds scanned bags of existing parts; audit writes off the bag not scanned",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const box = await seedStorage(request, { name: `E2E Box ${Date.now()}` });
    const bags = bagsWithMpn.slice(0, 3);
    expect(bags).toHaveLength(3);
    for (const [i, bag] of bags.entries()) {
      await seedPart(request, { name: `E2E Box Part ${i}`, mpn: bag.expected_mpn });
    }

    // Intake — camera off so the test does not wait on a camera permission.
    await page.addInitScript(() => localStorage.setItem("boxScan:camera", "off"));
    await page.goto(`/storage/${box.id}/intake`);
    for (const bag of bags) await scan(page, bag.raws[0]);
    // Labels without a Q field wait for a quantity before they are saved.
    await expect(page.getByText(/Added|Enter the quantity/)).toHaveCount(3, { timeout: 15_000 });
    const rows = page.getByTestId("intake-row");
    for (let i = 0; i < 3; i++) {
      const row = rows.nth(i);
      if ((await row.getByText("Enter the quantity, then Add").count()) === 0) continue;
      await row.getByLabel("Bag quantity").fill("5");
      await row.getByRole("button", { name: "Add" }).click();
      await expect(row.getByText("Added", { exact: true })).toBeVisible();
    }
    await expect(page.getByText("Added", { exact: true })).toHaveCount(3, { timeout: 15_000 });

    // Audit — scan two of the three bags, write off the third.
    await page.goto(`/storage/${box.id}/audit`);
    await expect(page.getByText("Not scanned (3)")).toBeVisible();
    await scan(page, bags[0].raws[0]);
    await scan(page, bags[1].raws[0]);
    await expect(page.getByText("Scanned (2)")).toBeVisible();
    await expect(page.getByText("Not scanned (1)")).toBeVisible();

    await page.getByRole("button", { name: "Finish audit" }).click();
    await page.getByRole("button", { name: "Apply" }).click();
    // The draft is cleared and the two remaining bags are the box contents now.
    await expect(page.getByText("Not scanned (2)")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Scanned (0)")).toBeVisible({ timeout: 15_000 });

    const contents = await request.get(`/api/storage/${box.id}/bags`);
    const body = (await contents.json()) as { data: unknown[] };
    expect(body.data).toHaveLength(2);
  },
);
