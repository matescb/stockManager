import { expect, type APIRequestContext } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

type Envelope<T> = {
  data: T;
  status: { category: string; message: string };
};

export type E2ERequest = APIRequestContext;

export type SeedPartPayload = {
  name?: string | null;
  part_type?: "linked" | "local" | "meta" | "sub_assembly";
  manufacturer?: string | null;
  mpn?: string | null;
  internal_part_number?: string | null;
  description?: string | null;
  notes_markdown?: string | null;
  footprint?: string | null;
  low_stock_report_quantity?: number | null;
  attrition_percentage?: number;
  attrition_min_quantity?: number;
  default_storage_location_id?: string | null;
  default_storage_mandatory?: boolean;
  serialized?: boolean;
};

export type SeedStoragePayload = {
  name?: string;
  description?: string | null;
  single_part_only?: boolean;
  existing_parts_only?: boolean;
  is_full?: boolean;
};

export type SeedStockPayload = {
  part_id: string;
  quantity: number;
  storage_location_id?: string | null;
  lot?: {
    name?: string | null;
    comments?: string | null;
    expiration_date?: string | null;
    serial_number?: string | null;
  };
  comments?: string | null;
  bag_signature?: string | null;
  raw_bag_code?: string | null;
};

export type SeedProjectPayload = {
  name: string;
  description?: string | null;
  notes_markdown?: string | null;
  associated_subassembly_part_id?: string | null;
};

export type SeedBomLinePayload = {
  entry_type?: "part" | "meta_part" | "non_part" | "unmatched";
  part_id?: string | null;
  meta_part_id?: string | null;
  name?: string | null;
  quantity?: number;
  comments?: string | null;
  designators?: string[];
  cad_footprint?: string | null;
  cad_key?: string | null;
  dnp?: boolean;
};

export type SeedScanImportPayload = {
  rows: Array<Record<string, unknown>>;
  idempotency_key?: string | null;
};

export type SeededPart = { id: string; name: string; [key: string]: unknown };
export type SeededStorage = { id: string; name: string; [key: string]: unknown };
export type SeededStockEntry = {
  id: string;
  part_id: string | null;
  quantity_delta: number;
  [key: string]: unknown;
};

function sameOriginHeaders() {
  return {
    origin: BASE_URL,
    referer: `${BASE_URL}/`,
  };
}

async function postJson<T>(
  request: APIRequestContext,
  path: string,
  payload: unknown,
  expectedStatus: number | number[] = [200, 201],
): Promise<T> {
  const response = await request.post(path, {
    data: payload,
    headers: sameOriginHeaders(),
  });
  const expected = Array.isArray(expectedStatus) ? expectedStatus : [expectedStatus];
  if (!expected.includes(response.status())) {
    throw new Error(`POST ${path} failed: ${response.status()} ${await response.text()}`);
  }
  const envelope = (await response.json()) as Envelope<T>;
  expect(envelope).toHaveProperty("data");
  expect(envelope).toHaveProperty("status");
  return envelope.data;
}

export async function seedPart(
  authedRequest: APIRequestContext,
  payload: SeedPartPayload = {},
): Promise<SeededPart> {
  return postJson<SeededPart>(authedRequest, "/api/parts", {
    name: "E2E Seed Part",
    part_type: "local",
    ...payload,
  });
}

export async function seedStorage(
  authedRequest: APIRequestContext,
  payload: SeedStoragePayload = {},
): Promise<SeededStorage> {
  return postJson<SeededStorage>(authedRequest, "/api/storage", {
    name: "E2E Seed Bin",
    ...payload,
  });
}

export async function seedStock(
  authedRequest: APIRequestContext,
  payload: SeedStockPayload,
): Promise<SeededStockEntry> {
  return postJson<SeededStockEntry>(authedRequest, "/api/stock/add", payload, 200);
}

export async function seedProject(
  _authedRequest: APIRequestContext,
  _payload: SeedProjectPayload,
): Promise<never> {
  throw new Error(
    "seedProject is reserved for E2E-3 and is intentionally not implemented in E2E-1.",
  );
}

export async function seedBomLine(
  _authedRequest: APIRequestContext,
  _projectId: string,
  _payload: SeedBomLinePayload,
): Promise<never> {
  throw new Error(
    "seedBomLine is reserved for E2E-3 and is intentionally not implemented in E2E-1.",
  );
}

export async function seedScanImport(
  _authedRequest: APIRequestContext,
  _payload: SeedScanImportPayload,
): Promise<never> {
  throw new Error(
    "seedScanImport is reserved for E2E-5 and is intentionally not implemented in E2E-1.",
  );
}
