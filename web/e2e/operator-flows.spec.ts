import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const STRONG_PW = "TestPass-2026-Stronk";
const RUN_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const ADMIN = {
  email: `e2e-aud040-admin-${RUN_ID}@x.com`,
  password: STRONG_PW,
  name: "AUD040 Admin",
};
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

type Envelope<T> = {
  data: T;
  status: { category: string; message: string };
};

type WorkspaceSignup = {
  user: { id: string; email: string; name: string };
  workspace_id: string;
};

type Part = { id: string; name: string; mpn: string | null };
type Project = { id: string; name: string };
type ProjectEntry = { id: string };
type Invitation = { id: string; token: string | null };
type Me = { workspaces: { id: string; name: string }[] };

let adminWorkspaceId = "";

async function api<T>(
  request: APIRequestContext,
  method: "get" | "post",
  path: string,
  body?: unknown,
): Promise<T> {
  const headers = { origin: BASE_URL, referer: `${BASE_URL}/` };
  const res = method === "get"
    ? await request.get(`/api${path}`)
    : await request.post(`/api${path}`, { data: body, headers });
  if (!res.ok()) {
    throw new Error(`${method.toUpperCase()} ${path} failed: ${res.status()} ${await res.text()}`);
  }
  const envelope = await res.json() as Envelope<T>;
  expect(envelope).toHaveProperty("data");
  expect(envelope).toHaveProperty("status");
  return envelope.data;
}

async function signup(
  request: APIRequestContext,
  email: string,
  password = STRONG_PW,
  name = "E2E User",
): Promise<WorkspaceSignup> {
  return api<WorkspaceSignup>(request, "post", "/auth/signup", {
    email,
    name,
    password,
    workspace_name: `${name} workspace ${RUN_ID}`,
  });
}

async function loginAsAdmin(page: Page) {
  await api(page.request, "post", "/auth/login", {
    email: ADMIN.email,
    password: ADMIN.password,
  });
}

async function createPart(page: Page, name: string, mpn: string): Promise<Part> {
  return api<Part>(page.request, "post", "/parts", {
    name,
    mpn,
    manufacturer: "E2E Fixtures",
  });
}

async function addStock(
  page: Page,
  partId: string,
  quantity: number,
  extra: Record<string, unknown> = {},
) {
  await api(page.request, "post", "/stock/add", {
    part_id: partId,
    quantity,
    ...extra,
  });
}

test.beforeAll(async ({ request }) => {
  const signupResult = await signup(request, ADMIN.email, ADMIN.password, ADMIN.name);
  adminWorkspaceId = signupResult.workspace_id;
});

test("@operator build consume completes a planned build from the SPA", async ({ page }) => {
  await loginAsAdmin(page);
  const suffix = `build-${RUN_ID}`;
  const part = await createPart(page, `E2E Build Resistor ${suffix}`, `E2E-BUILD-${suffix}`);
  await addStock(page, part.id, 5);
  const project = await api<Project>(page.request, "post", "/projects", {
    name: `E2E Build Project ${suffix}`,
  });
  await api<ProjectEntry>(page.request, "post", `/projects/${project.id}/entries`, {
    entry_type: "part",
    part_id: part.id,
    quantity: 2,
  });

  await page.goto("/builds/create");
  await page.getByLabel(/^name/i).fill(`E2E Build ${suffix}`);
  await page.getByLabel(/project/i).selectOption({ label: project.name });
  await page.getByLabel(/quantity/i).fill("2");
  await page.getByRole("button", { name: /create build/i }).click();

  await expect(page.getByText(`E2E Build ${suffix}`).first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /auto-fill/i }).click();
  await page.getByRole("button", { name: /consume & complete build/i }).click();

  await expect(page.getByText(/Build complete/i)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/^complete$/i).first()).toBeVisible();
});

test("@operator order receive posts stock from the order detail page", async ({ page }) => {
  await loginAsAdmin(page);
  const suffix = `order-${RUN_ID}`;
  const part = await createPart(page, `E2E Order Capacitor ${suffix}`, `E2E-ORDER-${suffix}`);

  await page.goto("/orders/create");
  await page.getByLabel(/^name/i).fill(`E2E Order ${suffix}`);
  await page.getByLabel(/supplier/i).fill("E2E Supplier");
  await page.getByRole("button", { name: /create order/i }).click();

  await expect(page.getByText(`E2E Order ${suffix}`).first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /\+ line/i }).click();
  await page.locator("#order-entry-part").selectOption({ label: `${part.name} — ${part.mpn}` });
  await page.getByLabel(/^qty$/i).fill("4");
  await page.getByRole("button", { name: /^add$/i }).click();
  await expect(page.getByText(part.name).first()).toBeVisible({ timeout: 10_000 });

  const receiveCard = page.locator(".card", { has: page.getByRole("heading", { name: "Receive" }) });
  await receiveCard.getByRole("spinbutton").fill("4");
  await receiveCard.getByRole("button", { name: /^receive$/i }).click();

  await expect(page.getByText("Received 4 units.")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/^received$/i).first()).toBeVisible();
});

test("@operator invite accept joins the invited workspace", async ({ page }) => {
  await loginAsAdmin(page);
  const inviteeEmail = `e2e-aud040-invitee-${RUN_ID}@x.com`;
  const workspaceName = `${ADMIN.name} workspace ${RUN_ID}`;
  const invitation = await api<Invitation>(page.request, "post", "/invitations", {
    email: inviteeEmail,
    role: "member",
  });
  expect(invitation.token).toBeTruthy();

  await api(page.request, "post", "/auth/logout");
  await page.goto("/login");
  await page.evaluate(() => localStorage.clear());
  await signup(page.request, inviteeEmail, STRONG_PW, "AUD040 Invitee");

  await page.goto("/settings/account");
  await page.getByPlaceholder(/paste token/i).fill(invitation.token!);
  await page.getByRole("button", { name: /^accept$/i }).click();
  await page.waitForURL(/\/parts(\b|$)/, { timeout: 10_000 });

  const me = await api<Me>(page.request, "get", "/auth/me");
  expect(me.workspaces.some((workspace) => workspace.id === adminWorkspaceId || workspace.name === workspaceName)).toBe(true);
});

test("@operator scan import bag rescan can consume from the scanned bag", async ({ page }) => {
  await loginAsAdmin(page);
  const suffix = `scan-${RUN_ID}`;
  const part = await createPart(page, `E2E Scan Diode ${suffix}`, `E2E-SCAN-${suffix}`);
  const bagSignature = "a".repeat(64);
  await addStock(page, part.id, 5, {
    bag_signature: bagSignature,
  });

  await page.addInitScript(
    ({ workspaceId, row }) => {
      localStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem(`scanImport:draft:${workspaceId}`, JSON.stringify({ v: 1, rows: [row] }));
    },
    {
      workspaceId: adminWorkspaceId,
      row: {
        rowId: `row-${suffix}`,
        bag: { mpn: part.mpn, manufacturer: "E2E Fixtures", quantity: 5, raw: `raw-${suffix}` },
        bagSig: bagSignature,
        quantity: 5,
        state: {
          kind: "bag_rescan",
          part_id: part.id,
          lot_id: null,
          storage_location_id: null,
          quantity: 5,
        },
      },
    },
  );

  await page.goto("/parts/scan-import");
  await expect(page.getByText(/Recognised/i)).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /increase quantity/i }).click();
  await page.getByRole("button", { name: /Remove 2/i }).click();

  await expect(page.getByText("Removed 2 from this bag.", { exact: true })).toBeVisible({ timeout: 10_000 });
  const stock = await api<{ total_on_hand: number }>(page.request, "get", `/parts/${part.id}/stock`);
  expect(stock.total_on_hand).toBe(3);
});
