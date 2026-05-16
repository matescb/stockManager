import { describe, expect, it } from "vitest";
import {
  OrderReceiveSchema,
  PartAddStockSchema,
  PartCreateSchema,
} from "../schemas";

const partId = "11111111-1111-1111-1111-111111111111";
const orderEntryId = "22222222-2222-2222-2222-222222222222";
const storageLocationId = "33333333-3333-3333-3333-333333333333";

describe("PartCreateSchema", () => {
  it("accepts a fixture matching backend PartIn", () => {
    expect(PartCreateSchema.parse({
      part_type: "linked",
      name: "10k resistor",
      manufacturer: "Yageo",
      mpn: "RC0402FR-0710KL",
      internal_part_number: "R-0402-10K",
      description: "Thick film resistor",
      notes_markdown: "Preferred part",
      footprint: "0402",
      low_stock_report_quantity: 25,
      attrition_percentage: 2.5,
      attrition_min_quantity: 3,
      default_storage_location_id: storageLocationId,
      default_storage_mandatory: true,
      serialized: false,
    })).toMatchInlineSnapshot(`
      {
        "attrition_min_quantity": 3,
        "attrition_percentage": 2.5,
        "default_storage_location_id": "33333333-3333-3333-3333-333333333333",
        "default_storage_mandatory": true,
        "description": "Thick film resistor",
        "footprint": "0402",
        "internal_part_number": "R-0402-10K",
        "low_stock_report_quantity": 25,
        "manufacturer": "Yageo",
        "mpn": "RC0402FR-0710KL",
        "name": "10k resistor",
        "notes_markdown": "Preferred part",
        "part_type": "linked",
        "serialized": false,
      }
    `);
  });

  it("rejects a bad payload", () => {
    expect(() => PartCreateSchema.parse({
      part_type: "component",
      name: "x".repeat(301),
      extra_field: true,
    })).toThrow();
  });
});

describe("OrderReceiveSchema", () => {
  it("accepts a fixture matching backend ReceiveIn", () => {
    expect(OrderReceiveSchema.parse({
      received_on: "2026-05-16",
      lines: [{
        order_entry_id: orderEntryId,
        quantity: 4,
        storage_location_id: storageLocationId,
        lot_name: "PO-100#1",
        serial_number: null,
      }],
    })).toMatchInlineSnapshot(`
      {
        "lines": [
          {
            "lot_name": "PO-100#1",
            "order_entry_id": "22222222-2222-2222-2222-222222222222",
            "quantity": 4,
            "serial_number": null,
            "storage_location_id": "33333333-3333-3333-3333-333333333333",
          },
        ],
        "received_on": "2026-05-16",
      }
    `);
  });

  it("rejects a bad payload", () => {
    expect(() => OrderReceiveSchema.parse({
      received_on: "2026-05-16",
      lines: [{
        order_entry_id: orderEntryId,
        quantity: 0,
      }],
    })).toThrow();
  });
});

describe("PartAddStockSchema", () => {
  it("accepts a fixture matching backend AddStockIn", () => {
    expect(PartAddStockSchema.parse({
      part_id: partId,
      quantity: 12,
      storage_location_id: storageLocationId,
      price: {
        mode: "per_component",
        unit_price: 0.125,
        total_price: null,
        currency: "USD",
      },
      lot: {
        name: "LOT-2026-001",
        comments: "supplier bag",
        expiration_date: "2027-01-31",
        serial_number: null,
      },
      comments: "manual receive",
      bag_signature: "a".repeat(64),
      raw_bag_code: null,
    })).toMatchInlineSnapshot(`
      {
        "bag_signature": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "comments": "manual receive",
        "lot": {
          "comments": "supplier bag",
          "expiration_date": "2027-01-31",
          "name": "LOT-2026-001",
          "serial_number": null,
        },
        "part_id": "11111111-1111-1111-1111-111111111111",
        "price": {
          "currency": "USD",
          "mode": "per_component",
          "total_price": null,
          "unit_price": 0.125,
        },
        "quantity": 12,
        "raw_bag_code": null,
        "storage_location_id": "33333333-3333-3333-3333-333333333333",
      }
    `);
  });

  it("rejects a bad payload", () => {
    expect(() => PartAddStockSchema.parse({
      part_id: partId,
      quantity: 0,
      bag_signature: "not-a-sha256",
    })).toThrow();
  });
});
