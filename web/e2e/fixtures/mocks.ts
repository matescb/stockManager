import { readFileSync } from "node:fs";
import type { Page, Route } from "@playwright/test";

type Envelope<T> = {
  data: T;
  status: { category: string; message: string };
};

const MPN_LOOKUP_ROUTE = "**/api/parts/lookup-mpn**";
const SOURCING_REFRESH_ROUTES = [
  "**/api/sourcing/**/refresh",
  "**/api/parts/**/sourcing/refresh",
] as const;
const PROJECT_SOURCING_ROUTE = "**/api/projects/**/sourcing";
const PROJECT_PURCHASE_PLAN_ROUTE = "**/api/projects/**/purchase-plan";
const PURCHASE_PLAN_ORDERS_ROUTE = "**/api/sourcing/purchase-plans/**/orders";

type MockSourcingOptions = {
  refreshResponse?: "success" | "stale";
};

async function fulfillJson(route: Route, payload: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

function fixture<T>(fileName: string): T {
  return JSON.parse(
    readFileSync(new URL(`./responses/${fileName}`, import.meta.url), "utf8"),
  ) as T;
}

function runtimeIso(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

function withRuntimeFields<T>(payload: T, route?: Route): T {
  const copy = structuredClone(payload) as T & {
    data?: {
      id?: string;
      project_id?: string;
      created_at?: string;
      expires_at?: string;
      last_refreshed_at?: string | null;
      fetched_at?: string;
    };
  };
  if (!copy.data) return copy;

  const now = runtimeIso();
  if ("fetched_at" in copy.data) {
    copy.data.fetched_at = now;
  }
  if ("created_at" in copy.data) {
    copy.data.created_at = now;
  }
  if ("last_refreshed_at" in copy.data) {
    copy.data.last_refreshed_at = now;
  }
  if ("expires_at" in copy.data) {
    copy.data.expires_at = runtimeIso(7 * 24 * 60 * 60 * 1000);
  }
  if (route && "project_id" in copy.data) {
    const match = route.request().url().match(/\/api\/projects\/([^/]+)\//);
    if (match) copy.data.project_id = match[1];
  }
  return copy;
}

export async function mockMpnLookup(page: Page, fixture: Envelope<unknown>) {
  await page.route(MPN_LOOKUP_ROUTE, async (route) => {
    await fulfillJson(route, fixture);
  });
}

export async function mockSourcingProviders(page: Page, options: MockSourcingOptions = {}) {
  const sourcingPayload = fixture<Envelope<unknown>>("sourcing.bom.json");
  const purchasePlanPayload = fixture<Envelope<unknown>>("sourcing.purchase-plan.json");
  const refreshPayload = fixture<Envelope<unknown>>("sourcing.refresh.json");
  const ordersPayload = fixture<Envelope<unknown>>("sourcing.orders.json");

  for (const pattern of SOURCING_REFRESH_ROUTES) {
    await page.route(pattern, async (route) => {
      if (options.refreshResponse === "stale") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            code: "sourcing.plan_stale",
            status: { category: "conflict", message: "Prices are stale." },
          }),
        });
        return;
      }
      await fulfillJson(route, withRuntimeFields(refreshPayload, route));
    });
  }
  await page.route(PROJECT_SOURCING_ROUTE, async (route) => {
    await fulfillJson(route, withRuntimeFields(sourcingPayload, route));
  });
  await page.route(PROJECT_PURCHASE_PLAN_ROUTE, async (route) => {
    await fulfillJson(route, withRuntimeFields(purchasePlanPayload, route));
  });
  await page.route(PURCHASE_PLAN_ORDERS_ROUTE, async (route) => {
    await fulfillJson(route, ordersPayload);
  });
}

export async function clearProviderMocks(page: Page) {
  await page.unroute(MPN_LOOKUP_ROUTE);
  for (const pattern of SOURCING_REFRESH_ROUTES) {
    await page.unroute(pattern);
  }
  await page.unroute(PROJECT_SOURCING_ROUTE);
  await page.unroute(PROJECT_PURCHASE_PLAN_ROUTE);
  await page.unroute(PURCHASE_PLAN_ORDERS_ROUTE);
}
