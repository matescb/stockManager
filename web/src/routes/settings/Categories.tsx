import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { PartCategoriesListSchema } from "@/lib/schemas";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";
import { Modal } from "@/components/Modal";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { PartCategory } from "@/types";

/** Request body for both create and edit — the backend accepts the same
 * field set on POST and PATCH. `library_slug` is omitted when blank so the
 * server derives it from the name. */
type CategoryBody = {
  name: string;
  description: string | null;
  sort_order: number;
  refdes_prefix: string | null;
  default_symbol_ref: string | null;
  default_footprint_ref: string | null;
  footprint_filters: string[] | null;
  library_slug?: string;
};

type FormState = {
  name: string;
  description: string;
  sort_order: string;
  refdes_prefix: string;
  default_symbol_ref: string;
  default_footprint_ref: string;
  footprint_filters: string;
  library_slug: string;
};

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  sort_order: "0",
  refdes_prefix: "",
  default_symbol_ref: "",
  default_footprint_ref: "",
  footprint_filters: "",
  library_slug: "",
};

function formFor(category: PartCategory): FormState {
  return {
    name: category.name,
    description: category.description ?? "",
    sort_order: String(category.sort_order),
    refdes_prefix: category.refdes_prefix ?? "",
    default_symbol_ref: category.default_symbol_ref ?? "",
    default_footprint_ref: category.default_footprint_ref ?? "",
    footprint_filters: (category.footprint_filters ?? []).join(", "),
    library_slug: category.library_slug,
  };
}

function trimmedOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function bodyFrom(form: FormState, { includeSlug }: { includeSlug: boolean }): CategoryBody {
  const filters = form.footprint_filters
    .split(",")
    .map(f => f.trim())
    .filter(Boolean);
  const body: CategoryBody = {
    name: form.name.trim(),
    description: trimmedOrNull(form.description),
    sort_order: Number(form.sort_order) || 0,
    refdes_prefix: trimmedOrNull(form.refdes_prefix),
    default_symbol_ref: trimmedOrNull(form.default_symbol_ref),
    default_footprint_ref: trimmedOrNull(form.default_footprint_ref),
    footprint_filters: filters.length > 0 ? filters : null,
  };
  // Blank means "derive from the name" on create. On edit the field is
  // pre-filled, so a blank slug can only be a user clearing it — also a
  // request to leave the stored value alone.
  const slug = form.library_slug.trim();
  if (includeSlug && slug) body.library_slug = slug;
  return body;
}

export default function CategoriesSettings() {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [showArchived, setShowArchived] = useState(false);
  const [editing, setEditing] = useState<PartCategory | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [err, setErr] = useState<string | null>(null);

  const categoriesQuery = useQuery({
    queryKey: useWsKey("categories", { archived: showArchived }),
    queryFn: ({ signal }) =>
      api.parsed.get(
        `/categories${showArchived ? "?include_archived=true" : ""}`,
        PartCategoriesListSchema,
        { signal },
      ),
  });
  const categories = categoriesQuery.data ?? [];

  function invalidate() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "categories") });
    // The parts list and every part detail render the category name.
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
  }

  function closeModal() {
    setCreating(false);
    setEditing(null);
    setForm(EMPTY_FORM);
    setErr(null);
  }

  function failed(e: ApiError) {
    const message = e instanceof ApiError ? e.userMessage : "Failed";
    setErr(message);
    toast.error(message);
  }

  const createMutation = useApiMutation<PartCategory, CategoryBody>({
    mutationKey: ["categories", "create"],
    mutationFn: (body) => api.post<PartCategory, CategoryBody>("/categories", body),
    onSuccess: () => {
      invalidate();
      toast.success("Category created.");
      closeModal();
    },
    onError: failed,
  });

  const updateMutation = useApiMutation<PartCategory, { id: string; body: CategoryBody }>({
    mutationKey: ["categories", "update"],
    mutationFn: ({ id, body }) => api.patch<PartCategory, CategoryBody>(`/categories/${id}`, body),
    onSuccess: () => {
      invalidate();
      toast.success("Category saved.");
      closeModal();
    },
    onError: failed,
  });

  const archiveMutation = useApiMutation<unknown, { id: string; restore: boolean }>({
    mutationKey: ["categories", "archive"],
    mutationFn: ({ id, restore }) =>
      api.post(`/categories/${id}/${restore ? "restore" : "archive"}`),
    onSuccess: (_res, { restore }) => {
      invalidate();
      toast.success(restore ? "Category restored." : "Category archived.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  function openCreate() {
    setForm(EMPTY_FORM);
    setErr(null);
    setEditing(null);
    setCreating(true);
  }

  function openEdit(category: PartCategory) {
    setForm(formFor(category));
    setErr(null);
    setCreating(false);
    setEditing(category);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!form.name.trim()) {
      setErr("Name is required.");
      return;
    }
    if (editing) {
      updateMutation.mutate({ id: editing.id, body: bodyFrom(form, { includeSlug: true }) });
    } else {
      createMutation.mutate(bodyFrom(form, { includeSlug: true }));
    }
  }

  async function toggleArchive(category: PartCategory) {
    const restore = category.archived_at !== null;
    const ok = await confirm({
      title: restore ? `Restore "${category.name}"?` : `Archive "${category.name}"?`,
      message: restore
        ? "The category becomes selectable again. If another category has taken its name or library slug in the meantime, the restore is refused."
        : "Parts keep their link to it, but the category stops appearing in pickers. Its name and library slug are freed for re-use.",
      severity: restore ? "warning" : "danger",
      confirmLabel: restore ? "Restore" : "Archive",
    });
    if (!ok) return;
    archiveMutation.mutate({ id: category.id, restore });
  }

  const modalOpen = creating || editing !== null;
  const saving = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="max-w-4xl">
      <h1 className="page-title mb-4">Categories</h1>
      <p className="text-sm text-muted mb-4">
        Buckets for the parts library. The reference-designator prefix and the
        default symbol / footprint references are the metadata a KiCad library
        is generated from; everything else is optional.
      </p>

      <InlineQueryError query={categoriesQuery} label="categories" className="mb-3" />

      <div className="card p-4 space-y-3">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={e => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
          <button type="button" className="btn-primary ml-auto" onClick={openCreate}>
            + Category
          </button>
        </div>

        {categoriesQuery.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : categories.length === 0 ? (
          <div className="text-muted text-sm">
            No categories yet. Create one to start grouping parts.
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Library slug</th>
                <th>Ref</th>
                <th>Symbol</th>
                <th>Footprint</th>
                <th>Order</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {categories.map(category => (
                <tr key={category.id} className={category.archived_at ? "opacity-50" : ""}>
                  <td>
                    <span className="font-medium">{category.name}</span>
                    {category.archived_at && <span className="pill ml-2 text-xs">Archived</span>}
                    {category.description && (
                      <div className="text-xs text-muted">{category.description}</div>
                    )}
                  </td>
                  <td className="font-mono text-xs">{category.library_slug}</td>
                  <td className="font-mono text-xs">{category.refdes_prefix ?? "—"}</td>
                  <td className="font-mono text-xs">{category.default_symbol_ref ?? "—"}</td>
                  <td className="font-mono text-xs">{category.default_footprint_ref ?? "—"}</td>
                  <td>{category.sort_order}</td>
                  <td className="whitespace-nowrap">
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => openEdit(category)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className={`ml-2 text-xs ${category.archived_at ? "btn" : "btn-danger"}`}
                      disabled={archiveMutation.isPending}
                      onClick={() => toggleArchive(category)}
                    >
                      {category.archived_at ? "Restore" : "Archive"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={editing ? "Edit category" : "Create category"}
        size="sm"
      >
        <form onSubmit={submit} className="p-4 space-y-3">
          <h2 className="card-title">
            {editing ? "Edit category" : "Create category"}
          </h2>
          {err && <div className="text-danger text-sm">{err}</div>}
          <div>
            <label className="label" htmlFor="category-name">Name</label>
            <input
              id="category-name"
              className="input"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            />
          </div>
          <div>
            <label className="label" htmlFor="category-slug">Library slug</label>
            <input
              id="category-slug"
              className="input font-mono"
              value={form.library_slug}
              placeholder="derived from the name"
              onChange={e => setForm(f => ({ ...f, library_slug: e.target.value }))}
            />
            <div className="text-xs text-muted mt-1">
              Lower-case letters, digits and dashes. Leave blank to derive it
              from the name. Renaming a category never moves its slug.
            </div>
          </div>
          <div>
            <label className="label" htmlFor="category-description">Description</label>
            <textarea
              id="category-description"
              className="input"
              rows={2}
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="category-refdes">Reference prefix</label>
              <input
                id="category-refdes"
                className="input"
                maxLength={10}
                placeholder="R, C, U…"
                value={form.refdes_prefix}
                onChange={e => setForm(f => ({ ...f, refdes_prefix: e.target.value }))}
              />
            </div>
            <div>
              <label className="label" htmlFor="category-sort">Sort order</label>
              <input
                id="category-sort"
                className="input"
                type="number"
                value={form.sort_order}
                onChange={e => setForm(f => ({ ...f, sort_order: e.target.value }))}
              />
            </div>
          </div>
          <div>
            <label className="label" htmlFor="category-symbol">Default symbol</label>
            <input
              id="category-symbol"
              className="input font-mono"
              placeholder="Device:R"
              value={form.default_symbol_ref}
              onChange={e => setForm(f => ({ ...f, default_symbol_ref: e.target.value }))}
            />
          </div>
          <div>
            <label className="label" htmlFor="category-footprint">Default footprint</label>
            <input
              id="category-footprint"
              className="input font-mono"
              placeholder="Resistor_SMD:R_0402_1005Metric"
              value={form.default_footprint_ref}
              onChange={e => setForm(f => ({ ...f, default_footprint_ref: e.target.value }))}
            />
          </div>
          <div>
            <label className="label" htmlFor="category-filters">Footprint filters</label>
            <input
              id="category-filters"
              className="input font-mono"
              placeholder="R_*, *_0402_*"
              value={form.footprint_filters}
              onChange={e => setForm(f => ({ ...f, footprint_filters: e.target.value }))}
            />
            <div className="text-xs text-muted mt-1">
              Comma-separated globs offered in KiCad&apos;s footprint chooser.
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : editing ? "Save" : "Create"}
            </button>
            <button type="button" className="btn" onClick={closeModal}>
              Cancel
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
