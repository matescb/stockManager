import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
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
  archived?: boolean;
  initial_qty?: number;
  storage_location_id?: string | null;
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
  bag_code: string;
  qty?: number;
  storage_location_id?: string | null;
  part_id?: string;
  part?: SeedPartPayload;
  mpn?: string;
  bag_signature?: string;
  raw_bag_code?: string | null;
};

export type SeededPart = { id: string; name: string; [key: string]: unknown };
export type SeededStorage = { id: string; name: string; [key: string]: unknown };
export type SeededStockEntry = {
  id: string;
  part_id: string | null;
  quantity_delta: number;
  [key: string]: unknown;
};

type BagSignatureFixture = {
  bags: Array<{
    expected_signature: string;
    expected_mpn?: string;
    expected_quantity?: number;
    raws: string[];
  }>;
};

const bagSignatureFixture = JSON.parse(
  readFileSync(new URL("../../src/lib/__fixtures__/bagSignatures.json", import.meta.url), "utf8"),
) as BagSignatureFixture;

function randomSuffix(): string {
  return randomUUID().slice(0, 8);
}

function fixtureForRawBag(rawBagCode: string) {
  return bagSignatureFixture.bags.find((bag) => bag.raws.includes(rawBagCode));
}

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
  const {
    archived = false,
    initial_qty,
    storage_location_id,
    ...partPayload
  } = payload;
  const part = await postJson<SeededPart>(authedRequest, "/api/parts", {
    name: `E2E Seed Part ${randomSuffix()}`,
    part_type: "local",
    ...partPayload,
  });

  if (initial_qty !== undefined) {
    if (initial_qty <= 0) {
      throw new Error("seedPart initial_qty must be greater than zero when provided.");
    }
    if (!storage_location_id) {
      throw new Error("seedPart initial_qty requires storage_location_id.");
    }
    await seedStock(authedRequest, {
      part_id: part.id,
      quantity: initial_qty,
      storage_location_id,
    });
  }

  if (archived) {
    await postJson<{
      archived_ids: string[];
      already_archived_ids: string[];
      not_found_ids: string[];
    }>(authedRequest, "/api/parts/bulk-delete", { part_ids: [part.id] });
  }

  return part;
}

export async function seedStorage(
  authedRequest: APIRequestContext,
  payload: SeedStoragePayload = {},
): Promise<SeededStorage> {
  return postJson<SeededStorage>(authedRequest, "/api/storage", {
    name: `E2E Seed Bin ${randomSuffix()}`,
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
  authedRequest: APIRequestContext,
  payload: SeedScanImportPayload,
): Promise<SeededStockEntry> {
  const fixture = fixtureForRawBag(payload.bag_code);
  const bagSignature = payload.bag_signature ?? fixture?.expected_signature;
  if (!bagSignature) {
    throw new Error("seedScanImport requires bag_signature or a bag_code from bagSignatures.json.");
  }

  const partId = payload.part_id ?? (await seedPart(authedRequest, {
    name: `E2E Bag Part ${randomSuffix()}`,
    mpn: payload.mpn ?? fixture?.expected_mpn ?? `E2E-BAG-${randomSuffix()}`,
    ...payload.part,
  })).id;

  return seedStock(authedRequest, {
    part_id: partId,
    quantity: payload.qty ?? fixture?.expected_quantity ?? 1,
    storage_location_id: payload.storage_location_id ?? null,
    bag_signature: bagSignature,
    raw_bag_code: payload.raw_bag_code === undefined ? payload.bag_code : payload.raw_bag_code,
  });
}
