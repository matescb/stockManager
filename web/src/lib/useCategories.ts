/**
 * One query for the workspace's part categories.
 *
 * Four screens fetched `/categories` independently before this existed —
 * each with its own URL and its own query key, so an edit on the settings
 * screen left the parts list showing the old name until something else
 * invalidated it. The tree makes that worse (a reparent changes what every
 * other screen should draw), so the fetch is consolidated here.
 *
 * `includeArchived` is part of the key, not just the URL: the two shapes
 * are different result sets and must not share a cache entry. The parts
 * list wants archived rows (a part can still point at one, and the label
 * would otherwise render blank); pickers do not.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { api } from "./api";
import { useWsKey, wsKeyOf } from "./queryKeys";
import { PartCategoriesListSchema, type PartCategory } from "./schemas";

export function categoriesKey(
  workspaceId: string | null | undefined,
  includeArchived: boolean,
): unknown[] {
  return wsKeyOf(workspaceId, "categories", { archived: includeArchived });
}

export function useCategories(
  { includeArchived = false }: { includeArchived?: boolean } = {},
): UseQueryResult<PartCategory[]> {
  return useQuery({
    queryKey: useWsKey("categories", { archived: includeArchived }),
    queryFn: ({ signal }) =>
      api.parsed.get(
        `/categories${includeArchived ? "?include_archived=true" : ""}`,
        PartCategoriesListSchema,
        { signal },
      ),
  });
}
