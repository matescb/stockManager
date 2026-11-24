# catalog_search fixtures

Recorded-SHAPE responses for `catalog_search` tests (SM-002): DigiKey Product
Information v4 `POST /products/v4/search/keyword`, `GET /products/v4/search/categories`,
and Mouser `POST /api/v1/search/keyword`.

The field names follow the v4 / Mouser schemas this codebase already maps
(`providers/digikey.py`, `providers/mouser.py`) plus KeywordResponse's
`ProductsCount` and `FilterOptions.{Manufacturers, Status, ParametricFilters,
TopCategories}`. The values (part numbers, counts, prices, ids) are
illustrative, not captured from a live call — no live key was used. Where a
field name could not be confirmed against a live response (the
`FilterValues[].ValueId/ValueName` pair, `TopCategories[].Category`, the
categories endpoint's `CategoryId`/`ChildCategories`), it matches DigiKey's
published v4 schema as best known; see the PR for the list.
