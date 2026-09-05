/**
 * `/settings/label-templates` — the label designer.
 *
 * Two modes, the shape the sibling skladVA project uses
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/index.tsx`):
 *  - list: every template in the workspace, filterable by entity type, with
 *    create / duplicate / delete / make-default;
 *  - editor: the WYSIWYG designer (`Editor.tsx`).
 *
 * Reads are member-visible; every mutation is admin-only server-side, so a
 * non-admin sees the list and gets a clear 403 message if they try to change
 * anything rather than a hidden-but-broken button.
 */
import { useMemo, useState, type ReactNode } from "react";
import { Copy, Plus, Star, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/DataTable";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import Editor from "./Editor";
import {
  useCreateTemplate,
  useDeleteTemplate,
  useLabelTemplates,
  useSeedDefaultTemplates,
  useSetDefaultTemplate,
} from "./data";
import { duplicateTemplate, starterTemplate, toDraft } from "./factory";
import {
  ENTITY_TYPE_LABELS,
  LABEL_ENTITY_TYPES,
  type LabelEntityType,
  type LabelTemplate,
  type TemplateDraft,
} from "./types";

type Filter = LabelEntityType | "all";

export default function LabelTemplatesPage() {
  const confirm = useConfirm();
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<TemplateDraft | null>(null);

  const templatesQuery = useLabelTemplates();
  const create = useCreateTemplate();
  const del = useDeleteTemplate();
  const setDefault = useSetDefaultTemplate();
  const seed = useSeedDefaultTemplates();

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);
  const rows = useMemo(
    () =>
      filter === "all"
        ? templates
        : templates.filter((tpl) => tpl.entity_type === filter),
    [templates, filter],
  );

  function failed(err: unknown, fallback: string) {
    const message = err instanceof ApiError ? err.userMessage : fallback;
    toast.error(message);
  }

  async function onDuplicate(tpl: LabelTemplate) {
    try {
      const created = await create.mutateAsync(duplicateTemplate(tpl));
      toast.success("Template duplicated.");
      setEditing(toDraft(created));
    } catch (err) {
      failed(err, "Could not duplicate the template.");
    }
  }

  async function onDelete(tpl: LabelTemplate) {
    const ok = await confirm({
      title: "Delete label template",
      message: `Delete "${tpl.name}"? Printing this ${ENTITY_TYPE_LABELS[
        tpl.entity_type
      ].toLowerCase()} will fall back to whatever other template is default.`,
      severity: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await del.mutateAsync({ id: tpl.id });
      toast.success("Template deleted.");
    } catch (err) {
      failed(err, "Could not delete the template.");
    }
  }

  async function onSetDefault(tpl: LabelTemplate) {
    try {
      await setDefault.mutateAsync({ id: tpl.id });
      toast.success(`"${tpl.name}" is now the default.`);
    } catch (err) {
      failed(err, "Could not set the default.");
    }
  }

  async function onSeed() {
    try {
      await seed.mutateAsync();
      toast.success("Built-in default templates are in place.");
    } catch (err) {
      failed(err, "Could not create the built-in templates.");
    }
  }

  if (editing) {
    return (
      <Editor
        initial={editing}
        onClose={() => setEditing(null)}
        onSaved={(saved) => setEditing(toDraft(saved))}
      />
    );
  }

  const busy = create.isPending || del.isPending || setDefault.isPending || seed.isPending;

  const columns: Column<LabelTemplate>[] = [
    {
      key: "name",
      header: "Template",
      accessor: (row) => row.name,
      render: (row) => (
        <span className="flex items-center gap-2">
          <span className="font-medium">{row.name}</span>
          {row.is_default && (
            <span className="pill bg-accent/15 text-accent">Default</span>
          )}
        </span>
      ),
    },
    {
      key: "entity_type",
      header: "For",
      accessor: (row) => ENTITY_TYPE_LABELS[row.entity_type],
    },
    {
      key: "size",
      header: "Media",
      accessor: (row) => `${row.width_mm}x${row.height_mm}`,
      render: (row) => (
        <span className="text-muted">
          {row.width_mm} x {row.height_mm} mm - {row.dpi} dpi
        </span>
      ),
    },
    {
      key: "elements",
      header: "Elements",
      align: "right",
      accessor: (row) => row.elements.length,
    },
    {
      key: "actions",
      header: "",
      width: "1%",
      render: (row) => (
        <div
          className="flex justify-end gap-1"
          // The row itself opens the editor; these buttons must not.
          onClick={(event) => event.stopPropagation()}
          role="presentation"
        >
          {!row.is_default && (
            <button
              type="button"
              className="btn-ghost btn-sm"
              title="Make default"
              aria-label={`Make "${row.name}" the default`}
              disabled={busy}
              onClick={() => onSetDefault(row)}
            >
              <Star size={14} />
            </button>
          )}
          <button
            type="button"
            className="btn-ghost btn-sm"
            title="Duplicate"
            aria-label={`Duplicate "${row.name}"`}
            disabled={busy}
            onClick={() => onDuplicate(row)}
          >
            <Copy size={14} />
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm text-danger"
            title="Delete"
            aria-label={`Delete "${row.name}"`}
            disabled={busy}
            onClick={() => onDelete(row)}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Label templates</h1>
      <p className="mb-4 max-w-3xl text-sm text-muted">
        Layouts for the cab SQUIX label printer, authored in millimetres. Each
        entity type has one default, which is the template the Print label
        action uses. A label&apos;s QR encodes the object&apos;s short code URL,
        so scanning it opens that object.
      </p>

      <div className="card mb-4 flex flex-wrap items-center gap-2 p-3">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by entity type">
          <FilterTab current={filter} value="all" onChange={setFilter}>
            All
          </FilterTab>
          {LABEL_ENTITY_TYPES.map((entity) => (
            <FilterTab key={entity} current={filter} value={entity} onChange={setFilter}>
              {ENTITY_TYPE_LABELS[entity]}
            </FilterTab>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <button type="button" className="btn" disabled={busy} onClick={onSeed}>
            <Wand2 size={15} />
            {seed.isPending ? "Creating…" : "Create built-in defaults"}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() =>
              setEditing(starterTemplate(filter === "all" ? "part" : filter))
            }
          >
            <Plus size={15} />
            New template
          </button>
        </div>
      </div>

      <QueryStateBoundary query={templatesQuery} resourceLabel="label templates">
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(row) => row.id}
          tableId="label-templates"
          exportFilename="label-templates"
          searchPlaceholder="Search templates…"
          onRowClick={(row) => setEditing(toDraft(row))}
          empty={
            <div className="p-6 text-center text-sm text-muted">
              No label templates yet. Use{" "}
              <span className="text-text">Create built-in defaults</span> to get
              one per entity type, or start from scratch.
            </div>
          }
        />
      </QueryStateBoundary>
    </div>
  );
}

function FilterTab({
  current,
  value,
  onChange,
  children,
}: {
  current: Filter;
  value: Filter;
  onChange: (value: Filter) => void;
  children: ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      className={active ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
      aria-pressed={active}
      onClick={() => onChange(value)}
    >
      {children}
    </button>
  );
}
