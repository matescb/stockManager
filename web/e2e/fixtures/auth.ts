import { randomUUID } from "node:crypto";
import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

import { DEFAULT_PASSWORD, EMAIL_DOMAIN, TIMEOUTS } from "./constants";

type SignupEnvelope = {
  data: {
    user: { id: string; email: string; name: string };
    workspace_id: string;
  };
  status: { category: string; message: string };
};

export type AuthedPage = {
  page: Page;
  request: APIRequestContext;
  email: string;
  workspaceId: string;
  userId: string;
};

type Fixtures = {
  authedPage: AuthedPage;
  authedRequest: APIRequestContext;
};

export const test = base.extend<Fixtures>({
  authedPage: async ({ page }, use, testInfo) => {
    const email = `e2e-${testInfo.testId}-${randomUUID().slice(0, 8)}@${EMAIL_DOMAIN}`;
    const response = await page.request.post("/api/auth/signup", {
      data: {
        email,
        name: "e2e",
        password: DEFAULT_PASSWORD,
      },
      timeout: TIMEOUTS.API_REQUEST_MS,
    });

    if (!response.ok()) {
      throw new Error(`signup failed: ${response.status()} ${await response.text()}`);
    }

    const envelope = (await response.json()) as SignupEnvelope;
    expect(envelope).toHaveProperty("data");
    expect(envelope).toHaveProperty("status");
    expect(envelope.data.user.email).toBe(email);

    await use({
      page,
      request: page.request,
      email,
      workspaceId: envelope.data.workspace_id,
      userId: envelope.data.user.id,
    });
  },

  authedRequest: async ({ authedPage }, use) => {
    await use(authedPage.request);
  },
});

export { expect };
