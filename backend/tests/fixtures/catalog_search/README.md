# catalog_search fixtures

Responses for `catalog_search` tests (SM-002): DigiKey Product Information v4
`POST /products/v4/search/keyword`, `GET /products/v4/search/categories`, and
Mouser `POST /api/v1/search/keyword`. Values (part numbers, counts, prices,
ids) are illustrative. No live key was used for any of them.

## DigiKey: where the shapes come from

Field names and nesting follow DigiKey's published ProductSearch v4 OpenAPI
definition (`KeywordResponse`, `FilterOptions`, `TopCategory`,
`TopCategoryNode`, `BaseFilterV4`, `ParameterFilterOptionsResponse`,
`FilterValue`, `CategoriesResponse`, `CategoryNode`). We read it from public
copies of DigiKey's swagger file:

- `wavenumber-eng/supply-chain-monkey@4da1137`,
  `docs/research/digikey/Digikey_ProductInfoV4_swagger.json`
- `Yamakaja/partrack@08ae6a5`, `doc/distributor/digikey/ProductSearch.json`

Which facets are **filled** follows recorded live KeywordResponses, not the
schema. The schema lists every facet, but a live answer leaves most of them
`null`:

| recording (repo@commit, path) | ProductsCount | Manufacturers | Status / ParametricFilters | TopCategories |
|---|---|---|---|---|
| `Yamakaja/partrack@99201d7`, `doc/distributor/digikey/examples/keyword_search.json` | 1 | filled | null | filled |
| `oletizi/audio-circuits`, `tests/fixtures/suppliers/digikey-keyword-2n3904.json` | 53 | filled | null | null |
| `oletizi/audio-circuits`, `tests/fixtures/suppliers/digikey-keyword-mfr-25fbf52-100k.json` | 1 | filled | null | null |
| `GavinMGlynn/cpu_datasheet_agent`, `test/fixtures/digikey/TPS54331.keyword.json` | 10 | filled | null | null |
| `krishna-swaroop/KiCAD-Prism`, `backend/tests/fixtures/supply/digikey_live_74ahc1g09gv.json` | 3 | filled | null | null |
| `krishna-swaroop/KiCAD-Prism`, `backend/tests/fixtures/supply/digikey_live_calls_lm358dr.json` | 8, one leaf category | filled | **filled** | null |

The LM358DR request sent `Keywords`/`Limit`/`Offset` only, so a filter in
the request is not what fills the facets. DigiKey fills them when the result
set is narrow. `retospect/precis-mcp@45f3c28` (`src/precis/supply/digikey.py`)
reads category parametric options by sending a leaf `CategoryFilter` with
empty keywords. It also records that DigiKey returns 400 "Parametric
Filtering Not Support at Parent Category Level" when a
`ParameterFilterRequest` names a parent category.

So:

- `digikey_keyword_broad.json` and `digikey_keyword_page2.json` have the
  broad-search shape: `Manufacturers` is filled and `Packaging`, `Status`,
  `Series`, `ParametricFilters` and `TopCategories` are `null`.
- `digikey_keyword_narrowed.json` has the narrow shape of the LM358DR
  recording: `Status`, `Packaging`, `Series` and `ParametricFilters` are
  filled, `ParametricFilters[].Category` is a `BaseFilterV4` (`{Id, Value}`),
  `FilterValues[]` is `{ProductCount, ValueId, ValueName}`, and a
  `UnitOfMeasure` parameter's `ValueId` is its text (`"0.1 µF"`).
  `TopCategories` is `null`.
- Products carry the nested `Category` that the recordings show
  (`CategoryId`, `ParentId`, `Name`, …, `ChildCategories`).
- The `TopCategories` shape (`RootCategory` / `Category` as
  `TopCategoryNode {Id, Name, ProductCount}`) is tested inline in
  `test_catalog_search_providers.py`, copied from the partrack recording.

## DigiKey 400 (`digikey_keyword_400_parent_category.json`)

The body is the v4 error schema `DKProblemDetails` (`type`, `title`,
`status`, `detail`, `instance`, `correlationId`, `errors`), which the
OpenAPI definition declares for every 4xx of `/search/keyword`. The `detail`
text is DigiKey's own, as `retospect/precis-mcp@dcd0fed`
(`tests/test_digikey_catalog.py`) records it. The other values are
illustrative. `type` and `instance` are there so tests can check that
URL-ish fields never reach the agent.
