/**
 * The "Specs" group in the parts table's Columns menu — pick which of a
 * category's canonical specs the list shows.
 *
 * ## Why it is in that menu and not a button of its own
 *
 * It used to be one: a separate "Spec columns" picker in the category
 * bar. Nobody found it. A column is a column, and the place everyone
 * looks for one is the table's own Columns menu, so the toggles live
 * there — as an extra section, next to the fixed part fields rather than
 * mixed into them, because the two mean different things.
 *
 * ## What a toggle here does
 *
 * The choice is stored on the CATEGORY, not in the browser. `DataTable`'s
 * own toggles persist to `localStorage` (`dataTableStorageKey`), so they
 * are per-viewer, per-device and invisible to anyone else; a curated
 * library is read by the whole workspace, and "resistors show resistance,
 * tolerance and power" is a fact about resistors rather than about a
 * laptop. So every toggle is a PATCH.
 *
 * It saves per toggle rather than behind a Save button because there is
 * nothing to review — one checkbox is one column — and a menu that
 * silently discards its state when it closes is worse than a request per
 * click on a setting nobody changes twice a day.
 *
 * ## Why the save is not over when the PATCH resolves
 *
 * Each payload is built from the CURRENT `list_columns`, which this
 * component reads back off the server. So the save is only finished once
 * `spec-schema` has refetched — until then the section is still
 * rendering the pre-save list, and a second toggle in that window would
 * build its payload from it and silently undo the first one.
 *
 * `onSuccess` therefore RETURNS the invalidations. TanStack awaits a
 * promise returned from `onSuccess` before settling the mutation, so
 * `isPending` — and with it every checkbox's `disabled` — stays true
 * across the refetch, and the window closes.
 *
 * `scope.id` is what actually serialises two mutations in TanStack v5;
 * `mutationKey` alone does not (it is a cache identity and a filter, not
 * a queue). It is set here as well, so two PATCHes cannot reach the
 * server out of order. Note that queueing is not freshness: a second
 * payload computed from a stale list is wrong whether it is sent first
 * or second, which is why the awaited invalidation above is the actual
 * fix and this is the belt to its braces.
 */
import { useMemo } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useAuth } from "@/lib/auth";
import { wsKeyOf } from "@/lib/queryKeys";
import type { ColumnMenuSection } from "@/components/DataTable";
import type { CategorySpecKey, CategorySpecSchema } from "@/lib/schemas";

/** Mirrors `MAX_LIST_COLUMNS` in `backend/app/domain/categories/schemas.py`. */
export const MAX_SPEC_COLUMNS = 12;

/**
 * The `Column.key` a spec key gets in the table.
 *
 * Namespaced so a spec key can never collide with a part field's, and
 * shared with the menu section so the section can claim its own columns
 * out of `DataTable`'s built-in list.
 */
export function specColumnKey(key: string): string {
  return `spec:${key}`;
}

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

/**
 * `capacitor_electrolytic` -> `electrolytic`, for the subtype badge.
 *
 * The class prefix is the same on every slug in a union, so repeating it
 * on every second row says nothing. What the reader needs is which
 * dielectric — or which channel type — the key belongs to.
 */
function subtypeOf(slug: string, componentClass: string | null): string {
  return slug.startsWith(`${componentClass}_`)
    ? slug.slice((componentClass as string).length + 1).replace(/_/g, " ")
    : slug;
}

/**
 * "electrolytic, tantalum" for a key only some of a class's slugs define.
 *
 * Only ever set on a ROOT category resolved as a class union (`slug` null,
 * `class` set). A key every slug defines gets no badge — it is simply a
 * key of the class — and neither does a category that resolved to one
 * slug, where every key belongs to it by construction.
 */
export function specSubtypeBadge(
  spec: CategorySpecKey,
  schema: CategorySpecSchema,
): string | undefined {
  if (schema.slug !== null || schema.class === null) return undefined;
  const everySlug = new Set(schema.keys.flatMap(k => k.slugs));
  if (spec.slugs.length === 0 || spec.slugs.length === everySlug.size) {
    return undefined;
  }
  return spec.slugs.map(slug => subtypeOf(slug, schema.class)).join(", ");
}

/**
 * The Columns-menu section for a category's spec keys, or null when
 * there is no category selected (and so no key vocabulary to offer).
 *
 * A hook rather than a component: `DataTable` owns the menu's markup, and
 * what it needs back is data. Called unconditionally — the mutation is
 * declared whether or not a category is selected, because a hook cannot
 * be skipped on a render.
 */
export function useSpecColumnsSection({
  categoryId,
  schema,
  categoryNames,
}: {
  categoryId: string | null;
  schema: CategorySpecSchema | undefined;
  /** `id -> name`, for the "Inherited from …" line. */
  categoryNames: ReadonlyMap<string, string>;
}): ColumnMenuSection | null {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();

  const save = useApiMutation<unknown, { list_columns: string[] }>({
    mutationKey: ["category", categoryId ?? "none", "list-columns"],
    scope: { id: `category-list-columns:${categoryId ?? "none"}` },
    mutationFn: payload => api.patch(`/categories/${categoryId}`, payload),
    // Returned, not fired and forgotten — see the module docstring. The
    // `categories` prefix covers both the categories listing and this
    // category's `spec-schema` entry; `parts` is a separate prefix
    // because the row payload itself changes (`?spec_columns=` moves
    // with it).
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "categories") }),
        qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") }),
      ]),
    onError: e => {
      toast.error(e instanceof ApiError ? e.userMessage : "Could not save columns");
    },
  });

  // Narrowed to keys the category's schema still HAS. A stored key can
  // outlive the schema — rename a category away from *Ceramic* and it
  // loses `dielectric` — and the server rightly refuses a `list_columns`
  // naming one. Without this, the stale key rides along on the next
  // toggle's payload and turns an unrelated tick into a 422. The parts
  // list drops it from the columns for the same reason.
  const selected = useMemo(() => {
    if (!schema) return [];
    const known = new Set(schema.keys.map(spec => spec.key));
    return (schema.list_columns ?? []).filter(key => known.has(key));
  }, [schema]);

  const saveMutate = save.mutate;
  const savePending = save.isPending;

  return useMemo(() => {
    if (categoryId === null || !schema) return null;
    const atCap = selected.length >= MAX_SPEC_COLUMNS;
    const inheritedFrom = schema.inherited_from
      ? categoryNames.get(schema.inherited_from) ?? "a parent category"
      : null;
    return {
      id: "spec-columns",
      title: "Specs",
      note: inheritedFrom
        ? `Inherited from ${inheritedFrom}. Changing these sets them on this category.`
        : undefined,
      emptyNote: "This category has no spec keys.",
      footer: atCap
        ? `At most ${MAX_SPEC_COLUMNS} spec columns. Uncheck one to add another.`
        : undefined,
      items: orderedSpecKeys(schema.keys).map(spec => {
        const checked = selected.includes(spec.key);
        return {
          key: specColumnKey(spec.key),
          label: specColumnHeader(spec),
          checked,
          disabled: savePending || (!checked && atCap),
          badge: spec.mandatory
            ? "required"
            : specSubtypeBadge(spec, schema),
          onToggle: () =>
            // Appended rather than re-sorted into schema order: the stored
            // list is ordered and that order is the column order, so the
            // user who added resistance last wanted it last.
            saveMutate({
              list_columns: checked
                ? selected.filter(k => k !== spec.key)
                : [...selected, spec.key],
            }),
        };
      }),
    };
  }, [categoryId, schema, selected, categoryNames, savePending, saveMutate]);
}
