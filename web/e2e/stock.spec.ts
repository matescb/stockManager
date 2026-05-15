import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import {
  expect,
  PartDetailPage,
  seedPart,
  seedScanImport,
  seedStock,
  seedStorage,
  test,
  type AuthedPage,
  type E2ERequest,
} from "./fixtures";

type Page = AuthedPage["page"];

type Envelope<T> = {
  data: T;
  status: { category: string; message: string };
};

type Part = {
  id: string;
  name: string;
  mpn: string | null;
};

type PartStock = {
  total_on_hand: number;
  rows: Array<{
    storage_location_id: string | null;
    lot_id: string | null;
    quantity: number;
  }>;
};

type StockEntry = {
  id: string;
  part_id: string | null;
  storage_location_id: string | null;
  quantity_delta: number;
  operation_type: string;
  occurred_at: string;
};

type BagSignatureFixture = {
  bags: Array<{
    expected_signature: string;
    expected_mpn?: string;
    expected_quantity?: number;
    raws: string[];
  }>;
};

const bagFixture = JSON.parse(
  readFileSync(new URL("../src/lib/__fixtures__/bagSignatures.json", import.meta.url), "utf8"),
) as BagSignatureFixture;

function uniqueName(prefix: string): string {
  return `${prefix} ${randomUUID().slice(0, 8)}`;
}

async function apiGet<T>(request: E2ERequest, path: string): Promise<T> {
  const response = await request.get(path);
  expect(response.ok()).toBe(true);
  const envelope = (await response.json()) as Envelope<T>;
  expect(envelope).toHaveProperty("data");
  expect(envelope).toHaveProperty("status");
  return envelope.data;
}

async function stockHistory(request: E2ERequest, partId: string): Promise<StockEntry[]> {
  const rows = await apiGet<StockEntry[]>(request, "/api/stock/history?limit=1000");
  return rows.filter((row) => row.part_id === partId);
}

async function partStock(request: E2ERequest, partId: string): Promise<PartStock> {
  return apiGet<PartStock>(request, `/api/parts/${partId}/stock`);
}

async function partsByMpn(request: E2ERequest, mpn: string): Promise<Part[]> {
  return apiGet<Part[]>(request, `/api/parts?mpn=${encodeURIComponent(mpn)}`);
}

async function addManualBagScan(page: Page, rawBagCode: string): Promise<void> {
  await page.getByRole("button", { name: /manual entry/i }).click();
  await page.getByLabel(/bag code/i).fill(rawBagCode);
  await page.getByRole("button", { name: /^add bag$/i }).click();
}

function fixtureBagWithMpn(index = 0) {
  const bag = bagFixture.bags[index];
  if (!bag?.expected_mpn) {
    throw new Error("Expected bagSignatures.json fixture entry with expected_mpn.");
  }
  return { raw: bag.raws[0], mpn: bag.expected_mpn };
}

test(
  "add stock writes ledger row and bumps on_hand",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const storage = await seedStorage(request, { name: uniqueName("A") });
    const part = await seedPart(request, { name: uniqueName("E2E Add Stock") });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("info");
    await detail.addStock(10, storage.name);
    await page.waitForURL(new RegExp(`/parts/${part.id}/stock$`));
    await expect(detail.onHandValue).toHaveText("10");

    await detail.openTab("History");
    await expect(detail.historyRows).toHaveCount(1);
    await expect(detail.historyRows.first()).toContainText("+10");
    await expect(detail.historyRows.first()).toContainText(storage.name);
  },
);

test(
  "second add appends a row; on_hand is the sum",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const storage = await seedStorage(request, { name: uniqueName("A") });
    const part = await seedPart(request, { name: uniqueName("E2E Add Twice") });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("info");
    await detail.addStock(4, storage.name);
    await page.waitForURL(new RegExp(`/parts/${part.id}/stock$`));
    await detail.addStock(6, storage.name);
    await page.waitForURL(new RegExp(`/parts/${part.id}/stock$`));

    await expect(detail.onHandValue).toHaveText("10");
    await detail.openTab("History");
    await expect(detail.historyRows).toHaveCount(2);
    await expect(detail.historyRows.filter({ hasText: "+4" })).toHaveCount(1);
    await expect(detail.historyRows.filter({ hasText: "+6" })).toHaveCount(1);
  },
);

test(
  "zero / negative quantity is rejected by HTML5 min=1",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    await seedStorage(request, { name: uniqueName("A") });
    const part = await seedPart(request, { name: uniqueName("E2E Invalid Qty") });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("add");
    const form = page.locator("form");
    await page.getByLabel(/quantity/i).fill("0");
    await page.getByRole("button", { name: /^add$/i }).click();
    expect(await form.evaluate((node) => (node as HTMLFormElement).checkValidity())).toBe(false);
    await expect(page).toHaveURL(new RegExp(`/parts/${part.id}/add$`));

    await page.getByLabel(/quantity/i).fill("-3");
    await page.getByRole("button", { name: /^add$/i }).click();
    expect(await form.evaluate((node) => (node as HTMLFormElement).checkValidity())).toBe(false);
    await expect(page).toHaveURL(new RegExp(`/parts/${part.id}/add$`));

    await detail.openTab("Stock");
    await expect(detail.onHandValue).toHaveText("0");
  },
);

test(
  "move between locations writes a +/- ledger pair",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const source = await seedStorage(request, { name: uniqueName("A") });
    const destination = await seedStorage(request, { name: uniqueName("B") });
    const part = await seedPart(request, { name: uniqueName("E2E Move Stock") });
    await seedStock(request, {
      part_id: part.id,
      quantity: 5,
      storage_location_id: source.id,
    });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("info");
    await detail.moveStock(3, source.name, destination.name);
    await page.waitForURL(new RegExp(`/parts/${part.id}/stock$`));
    await expect(detail.onHandValue).toHaveText("5");

    await detail.openTab("History");
    await expect(detail.historyRows).toHaveCount(3);
    await expect(detail.historyRows.filter({ hasText: "-3" }).filter({ hasText: source.name })).toHaveCount(1);
    await expect(detail.historyRows.filter({ hasText: "+3" }).filter({ hasText: destination.name })).toHaveCount(1);
  },
);

test(
  "From-location dropdown is empty when no on-hand stock",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    await seedStorage(request, { name: uniqueName("A") });
    const part = await seedPart(request, { name: uniqueName("E2E Empty Move") });
    const detail = new PartDetailPage(page, part.id);

    await detail.goto("move");

    await expect(page.getByText("Nothing on hand for this part.")).toBeVisible();
    await expect(page.locator("#move-stock-from")).toHaveCount(0);
  },
);

test(
  'bag rescan shows "Found bag" UI, does not create a new part',
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const bag = fixtureBagWithMpn();
    const part = await seedPart(request, {
      name: uniqueName("E2E Bag Rescan"),
      mpn: bag.mpn,
    });
    await seedScanImport(request, {
      part_id: part.id,
      bag_code: bag.raw,
      qty: 5,
    });

    await page.goto("/parts/scan-import");
    await addManualBagScan(page, bag.raw);

    const rescanRow = page.getByTestId("scan-row-bag-rescan");
    await expect(rescanRow).toBeVisible({ timeout: 15_000 });
    await expect(rescanRow).toContainText("Recognised");
    await expect(rescanRow).toContainText("5");
    await expect(rescanRow.getByRole("button", { name: /open part/i })).toBeVisible();
    expect(await stockHistory(request, part.id)).toHaveLength(1);
    expect(await partsByMpn(request, bag.mpn)).toHaveLength(1);
  },
);

test(
  "quick-remove from rescanned bag decrements the existing entry",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const bag = fixtureBagWithMpn();
    const part = await seedPart(request, {
      name: uniqueName("E2E Bag Quick Remove"),
      mpn: bag.mpn,
    });
    await seedScanImport(request, {
      part_id: part.id,
      bag_code: bag.raw,
      qty: 5,
    });

    await page.goto("/parts/scan-import");
    await addManualBagScan(page, bag.raw);

    const rescanRow = page.getByTestId("scan-row-bag-rescan");
    await expect(rescanRow).toBeVisible({ timeout: 15_000 });
    await rescanRow.getByRole("spinbutton").fill("5");
    await rescanRow.getByRole("button", { name: /^remove 5$/i }).click();

    await expect(page.getByText("Removed 5 from this bag.").first()).toBeVisible({ timeout: 10_000 });
    await expect.poll(async () => (await partStock(request, part.id)).total_on_hand).toBe(0);
    const rows = await stockHistory(request, part.id);
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.quantity_delta).sort((a, b) => a - b)).toEqual([-5, 5]);
  },
);

test.describe("stock history ordering", () => {
  test.describe.configure({ mode: "serial" });

  test(
    "stock history per-part filter renders rows in newest-first order",
    { tag: ["@core"] },
    async ({ authedPage }) => {
      const { page, request } = authedPage;
      const storage = await seedStorage(request, { name: uniqueName("A") });
      const part = await seedPart(request, { name: uniqueName("E2E History Order") });
      const detail = new PartDetailPage(page, part.id);

      for (const [index, quantity] of [1, 2, 3].entries()) {
        await seedStock(request, {
          part_id: part.id,
          quantity,
          storage_location_id: storage.id,
        });
        if (index < 2) {
          await new Promise((resolve) => setTimeout(resolve, 5));
        }
      }

      await detail.goto("history");

      await expect(detail.historyRows).toHaveCount(3);
      await expect(detail.historyRows.nth(0)).toContainText("+3");
      await expect(detail.historyRows.nth(1)).toContainText("+2");
      await expect(detail.historyRows.nth(2)).toContainText("+1");
    },
  );
});
