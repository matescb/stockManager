# E2E Fixture Responses

Audience: engineer

Canned provider payloads for Playwright route mocks.

| File | Source fixture |
|---|---|
| `lookup-mpn-success.json` | Stubbed from the `MpnLookupResult` shape in `web/src/types.ts`; E2E-4/E2E-5 replace or extend this with provider-specific integration fixtures. |

Payloads include the API envelope (`{ data, status }`) because `web/src/lib/api.ts` unwraps `data` and treats malformed responses as failures.
