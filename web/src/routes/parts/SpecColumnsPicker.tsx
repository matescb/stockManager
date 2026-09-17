/**
 * "Spec columns" — pick which of a category's canonical specs the parts
 * list shows, and save a default sort.
 *
 * The choice is stored on the CATEGORY, not in the browser. `DataTable`'s
 * own Columns menu persists to `localStorage` (`dataTableStorageKey`), so
 * it is per-viewer, per-device and invisible to anyone else; a curated
 * library is read by the whole workspace, and "resistors show resistance,
 * tolerance and power" is a fact about resistors rather than about a
 * laptop. So every toggle is a PATCH.
 *
 * It saves per toggle rather than behind a Save button because there is
 * nothing to review — one checkbox is one column — and a menu that
 * silently discards its state when it closes is worse than a request per
 * click on a setting nobody changes twice a day. `useApiMutation`'s
 * `mutationKey` serialises the clicks, so a fast double-toggle cannot
 * race two PATCHes.
 */
import { useMemo } from "react";
import { toast } from "sonner";
import { Columns3 } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useAuth } from "@/lib/auth";
import { wsKeyOf } from "@/lib/queryKeys";
import { useQueryClient } from "@tanstack/react-query";
import type { CategorySpecKey, CategorySpecSchema } from "@/lib/schemas";

/** Mirrors `MAX_LIST_COLUMNS` in `backend/app/domain/categories/schemas.py`. */
export const MAX_SPEC_COLUMNS = 12;

/** `Resistance (Ω)` — the unit belongs in the header, not in every cell. */
export function specColumnHeader(spec: {
  label: string;
  unit: string | null;
}): string {
  return spec.unit ? `${spec.label} (${spec.unit})` : spec.label;
}

/**
 * Mandatory keys first, each group in schema order.
 *
 * The schema lists common keys before the category's own, which puts
 * `Package` and `Mounting` above `Resistance` — exactly backwards for
 * someone who opened this menu to add resistance. Mandatory means "this
 * category says a part must have it", which is the closest thing the
 * schema has to "this is what you came for".
 */
export function orderedSpecKeys(
  keys: readonly CategorySpecKey[],
): CategorySpecKey[] {
  return [...keys.filter(k => k.mandatory), ...keys.filter(k => !k.mandatory)];
}

export default function SpecColumnsPicker({
  categoryId,
  schema,
  categoryNames,
}: {
  categoryId: string;
  schema: CategorySpecSchema;
  /** `id -> name`, for the "inherited from …" line. */
  categoryNames: ReadonlyMap<string, string>;
}) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const selected = schema.list_columns ?? [];
  const ordered = useMemo(() => orderedSpecKeys(schema.keys), [schema.keys]);

  const save = useApiMutation<unknown, { list_columns: string[] }>({
    mutationKey: ["category", categoryId, "list-columns"],
    mutationFn: payload => api.patch(`/categories/${categoryId}`, payload),
    onSuccess: () => {
      // The prefix covers both the categories listing and this category's
      // `spec-schema` entry; `parts` is a separate prefix because the row
      // payload itself changes (`?spec_columns=` moves with it).
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "categories") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
    },
    onError: e => {
      toast.error(e instanceof ApiError ? e.userMessage : "Could not save columns");
    },
  });

  function toggle(key: string) {
    // Appended rather than re-sorted into schema order: the stored list is
    // ordered and that order is the column order, so the user who added
    // resistance last wanted it last.
    const next = selected.includes(key)
      ? selected.filter(k => k !== key)
      : [...selected, key];
    save.mutate({ list_columns: next });
  }

  const atCap = selected.length >= MAX_SPEC_COLUMNS;
  const inheritedFrom = schema.inherited_from
    ? categoryNames.get(schema.inherited_from) ?? "a parent category"
    : null;

  return (
    <details className="relative">
      <summary className="btn btn-sm cursor-pointer list-none inline-flex items-center gap-1.5">
        <Columns3 size={14} />
        Spec columns
        {selected.length > 0 && <span className="pill">{selected.length}</span>}
      </summary>
      <div className="absolute left-0 top-full mt-1 z-20 card p-2 min-w-[260px] max-h-[60vh] overflow-y-auto">
        {ordered.length === 0 ? (
          <p className="px-2 py-1 text-sm text-muted">
            This category has no spec keys.
          </p>
        ) : (
          <>
            {inheritedFrom && (
              <p className="px-2 py-1 text-xs text-muted">
                Inherited from {inheritedFrom}. Changing these sets them on this
                category.
              </p>
            )}
            {ordered.map(spec => {
              const checked = selected.includes(spec.key);
              return (
                <label
                  key={spec.key}
                  className="flex items-center gap-2 px-2 py-1 text-sm cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={save.isPending || (!checked && atCap)}
                    onChange={() => toggle(spec.key)}
                  />
                  <span>{specColumnHeader(spec)}</span>
                  {spec.mandatory && (
                    <span className="text-xs text-muted">required</span>
                  )}
                </label>
              );
            })}
            {atCap && (
              <p className="px-2 py-1 text-xs text-muted">
                At most {MAX_SPEC_COLUMNS} spec columns. Uncheck one to add
                another.
              </p>
            )}
          </>
        )}
      </div>
    </details>
  );
}
