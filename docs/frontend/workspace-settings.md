# Workspace Settings

Audience: engineer

Frontend notes for `/settings/workspace` cards that edit workspace-scoped configuration.

## Sourcing Provider

The Workspace page mounts `SourcingCard` below the parts data provider card.
Source: `web/src/routes/settings/Workspace.tsx:655`.

The card edits TrustedParts sourcing preferences with provider, masked CompanyId
and API key inputs, country, currency, preferred distributors, and dashboard
cache preference. Save calls `api.patch("/workspaces/current", body)` and
invalidates the workspace-current query; test connection calls
`api.post("/workspaces/current/sourcing/test", {})`.
Source: `web/src/routes/settings/SourcingCard.tsx:85-134`.

Credential values are never read back from the API. The UI clears typed
credential fields after save and displays only the `Configured ✓` pill when
`has_sourcing_company_id && has_sourcing_api_key` is true.
Source: `web/src/routes/settings/SourcingCard.tsx:70-82`,
`web/src/routes/settings/SourcingCard.tsx:137-147`.
