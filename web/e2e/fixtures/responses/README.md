# E2E Fixture Responses

Audience: engineer

Canned provider payloads for Playwright route mocks.

| File | Source fixture |
|---|---|
| `lookup-mpn-success.json` | Stubbed from the `MpnLookupResult` shape in `web/src/types.ts`; E2E-4/E2E-5 replace or extend this with provider-specific integration fixtures. |
| `sourcing.bom.json` | Lifted from the fake TrustedParts offer shapes and asserted BOM response in `backend/tests/test_sourcing_bom_route.py`; provider URLs sanitized to `example.test`. |
| `sourcing.purchase-plan.json` | Lifted from `backend/tests/test_purchase_plan_route.py` and `backend/tests/test_purchase_plan_convert_route.py` purchase-plan line/offer assertions; provider URLs sanitized to `example.test`. |
| `sourcing.refresh.json` | Lifted from `backend/tests/test_purchase_plan_refresh_route.py`; keeps offer identity stable while mutating the second line price for refresh evidence. |
| `sourcing.orders.json` | Lifted from the order response shape asserted in `backend/tests/test_purchase_plan_convert_route.py`. |

Payloads include the API envelope (`{ data, status }`) because `web/src/lib/api.ts` unwraps `data` and treats malformed responses as failures.
