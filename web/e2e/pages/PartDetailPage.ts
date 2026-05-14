import type { AuthedPage } from "../fixtures";

type Page = AuthedPage["page"];
type Locator = ReturnType<Page["locator"]>;

export type PartTab =
  | "info"
  | "specs"
  | "stock"
  | "add"
  | "move"
  | "remove"
  | "history"
  | "settings";

export type PartTabLabel =
  | "Part info"
  | "Specs"
  | "Stock"
  | "Add stock"
  | "Move stock"
  | "Remove stock"
  | "History"
  | "Settings";

export class PartDetailPage {
  constructor(readonly page: Page, readonly partId: string) {}

  async goto(tab: PartTab = "info"): Promise<void> {
    await this.page.goto(`/parts/${this.partId}/${tab}`);
  }

  async openTab(label: PartTabLabel): Promise<void> {
    await this.page.getByRole("link", { name: label, exact: true }).click();
  }

  async addStock(qty: number, storageName?: string): Promise<void> {
    await this.openTab("Add stock");
    await this.page.getByLabel(/quantity/i).fill(String(qty));
    if (storageName) {
      await this.page.getByLabel(/storage/i).selectOption({ label: storageName });
    }
    await this.page.getByRole("button", { name: /^add$/i }).click();
  }

  async moveStock(qty: number, fromStorageName: string, toStorageName: string): Promise<void> {
    await this.openTab("Move stock");
    const sourceSelect = this.page.locator("#move-stock-from");
    const sourceValue = await sourceSelect
      .locator("option", { hasText: fromStorageName })
      .first()
      .getAttribute("value");
    await sourceSelect.selectOption(sourceValue ?? "");
    await this.page.getByLabel(/to storage/i).selectOption({ label: toStorageName });
    await this.page.getByLabel(/quantity/i).fill(String(qty));
    await this.page.getByRole("button", { name: /^move$/i }).click();
  }

  get onHandValue(): Locator {
    return this.page.getByTestId("part-stock-on-hand");
  }

  get historyRows(): Locator {
    return this.page.locator("table tbody tr");
  }

  get stockRows(): Locator {
    return this.page.locator("table tbody tr");
  }

  get header(): Locator {
    return this.page.locator(".card").first();
  }

  subNav(label: PartTabLabel): Locator {
    return this.page.getByRole("link", { name: label, exact: true });
  }
}
