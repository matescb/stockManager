/**
 * One query for a category's spec schema and its saved list settings.
 *
 * `GET /api/categories/{id}/spec-schema` answers three things the parts
 * list needs and cannot work out for itself: which canonical spec keys
 * this category's parts carry (the key vocabulary lives in
 * `backend/app/domain/parts/spec_schema_tables.py`, not on the wire),
 * which of them are configured as columns, and which ancestor that
 * configuration came from.
 *
 * Keyed under `["categories", id, "spec-schema"]` so the picker's PATCH —
 * which invalidates the `["categories"]` prefix — refreshes it without
 * naming it, the way every other mutation in the app invalidates by
 * prefix.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { CategorySpecSchemaSchema, type CategorySpecSchema } from "@/lib/schemas";

export function useCategorySpecSchema(
  categoryId: string | null,
): UseQueryResult<CategorySpecSchema> {
  return useQuery({
    queryKey: useWsKey("categories", categoryId ?? "none", "spec-schema"),
    // No category selected means no schema to ask for: the vocabulary IS
    // the category's, and `/parts` ignores the spec params without one.
    enabled: categoryId !== null,
    queryFn: ({ signal }) =>
      api.parsed.get(
        `/categories/${encodeURIComponent(categoryId as string)}/spec-schema`,
        CategorySpecSchemaSchema,
        { signal },
      ),
  });
}
