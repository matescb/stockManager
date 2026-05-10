# Bundled External Schemas

Audience: engineer

Pinned third-party API schemas used for drift checks and generated code.

## TrustedParts Inventory API v2

`trustedparts-v2.json` is the pretty-printed OpenAPI document served by
TrustedParts' official Swagger UI:
`https://api.trustedparts.com/swagger/inventory-api-v2/swagger.json`.

Refresh and regenerate the checked-in Pydantic models from the repository root:

```bash
make refresh-tp-spec
make regen-tp-models
```

`make refresh-tp-spec` sorts JSON keys with `jq` so the bundled schema is stable.
Run it only when intentionally refreshing from TrustedParts. `make regen-tp-models` writes
`backend/app/domain/sourcing/_generated/trustedparts_v2.py` with a fixed
AUTO-GENERATED header. The CI `tp-schema-drift` job regenerates models from the
bundled schema and fails if generated models differ from the committed copies.
CI does not fetch the live TrustedParts schema.

Do not wire these generated models into
`backend/app/domain/sourcing/client.py` in the schema-foundation PR; issue #449
owns that integration.
