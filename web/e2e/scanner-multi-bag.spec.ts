import { readFileSync } from "node:fs";

import {
  expect,
  seedPart,
  seedScanImport,
  test,
  type AuthedPage,
} from "./fixtures";

type Page = AuthedPage["page"];

type BagSignatureFixture = {
  bags: Array<{
    expected_mpn?: string;
    raws: string[];
  }>;
};

const bagFixture = JSON.parse(
  readFileSync(new URL("../src/lib/__fixtures__/bagSignatures.json", import.meta.url), "utf8"),
) as BagSignatureFixture;

function fixtureBagWithMpn(index: number) {
  const bag = bagFixture.bags[index];
  const raw = bag?.raws[0];
  if (!raw || !bag.expected_mpn) {
    throw new Error(`Expected bagSignatures.json fixture entry ${index} with raw code and expected_mpn.`);
  }
  return { raw, mpn: bag.expected_mpn };
}

async function scanBagFromKeyboard(page: Page, input: ReturnType<Page["getByLabel"]>, rawBagCode: string) {
  await input.focus();
  await expect(input).toBeFocused();
  await page.keyboard.insertText(rawBagCode);
  await page.keyboard.press("Enter");
}

test(
  "scanner multi-bag focus stays on manual barcode input after sequential scans and Escape",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const bags = [fixtureBagWithMpn(0), fixtureBagWithMpn(3)];

    for (const [index, bag] of bags.entries()) {
      const part = await seedPart(request, {
        name: `E2E Multi Bag Focus ${index}`,
        mpn: bag.mpn,
      });
      await seedScanImport(request, {
        part_id: part.id,
        bag_code: bag.raw,
        qty: index + 3,
      });
    }

    await page.goto("/parts/scan-import");
    await page.getByRole("button", { name: /manual entry/i }).click();

    const input = page.getByLabel(/bag code/i);
    const rescanRows = page.getByTestId("scan-row-bag-rescan");
    await expect(input).toBeVisible();

    await scanBagFromKeyboard(page, input, bags[0].raw);
    await expect(rescanRows).toHaveCount(1, { timeout: 15_000 });
    await expect(input).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(page).toHaveURL(/\/parts\/scan-import$/);
    await expect(input).toBeVisible();
    await expect(input).toBeFocused();
    await expect(rescanRows).toHaveCount(1);

    await scanBagFromKeyboard(page, input, bags[1].raw);
    await expect(rescanRows).toHaveCount(2, { timeout: 15_000 });
    await expect(input).toBeFocused();

    for (const bag of bags) {
      await expect(page.getByText(bag.mpn, { exact: true })).toBeVisible();
    }
  },
);
