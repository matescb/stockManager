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

function okEnvelope<T>(data: T): Envelope<T> {
  return {
    data,
    status: { category: "ok", message: "OK" },
  };
}

async function fulfillJson(route: Route, payload: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

export async function mockMpnLookup(page: Page, fixture: Envelope<unknown>) {
  await page.route(MPN_LOOKUP_ROUTE, async (route) => {
    await fulfillJson(route, fixture);
  });
}

export async function mockSourcingProviders(page: Page, { offers }: { offers: unknown }) {
  const payload = okEnvelope(offers);
  for (const pattern of SOURCING_REFRESH_ROUTES) {
    await page.route(pattern, async (route) => {
      await fulfillJson(route, payload);
    });
  }
  await page.route(PROJECT_SOURCING_ROUTE, async (route) => {
    await fulfillJson(route, payload);
  });
}

export async function clearProviderMocks(page: Page) {
  await page.unroute(MPN_LOOKUP_ROUTE);
  for (const pattern of SOURCING_REFRESH_ROUTES) {
    await page.unroute(pattern);
  }
  await page.unroute(PROJECT_SOURCING_ROUTE);
}
