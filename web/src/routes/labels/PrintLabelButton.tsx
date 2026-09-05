/**
 * "Print label" action for an object detail page.
 *
 * Wraps `POST /api/label-templates/{id}/test-print` with an `entity_id`, which
 * is the object-print path: the server renders that object's real data, mints
 * its short code (get-or-create, same as `POST /api/codes`) and ships the
 * JScript. There is no separate "print this part" endpoint and this PR does
 * not add one.
 *
 * The failure path is the point of this component. Printing is DISABLED in
 * production right now (`PRINT_HOST` empty — see CLAUDE.md and
 * `docs/deployment.md` "Label printer connectivity"), so the realistic outcome
 * is a 409 `printer.unreachable` carrying a `print_job_id`. That must read as
 * "nothing printed, the attempt is on record", never as a crash and never as a
 * silent success.
 */
import { useMemo, useState } from "react";
import { Printer } from "lucide-react";
import { toast } from "sonner";
import { Modal } from "@/components/Modal";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import {
  pickDefaultTemplate,
  printErrorMessage,
  useLabelTemplates,
  usePrintLabel,
} from "./data";
import { CopiesField, TemplateField } from "./printFields";
import { ENTITY_TYPE_LABELS, type LabelEntityType } from "./types";

interface PrintLabelButtonProps {
  entityType: LabelEntityType;
  entityId: string;
  /** Shown in the dialog so the operator can confirm what they're labelling. */
  entityName?: string | null;
  className?: string;
}

export default function PrintLabelButton({
  entityType,
  entityId,
  entityName,
  className,
}: PrintLabelButtonProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={className ?? "btn inline-flex items-center gap-1.5"}
        onClick={() => setOpen(true)}
      >
        <Printer size={14} />
        Print label
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Print label"
        size="sm"
      >
        {/* Mounted only while open, so the template list is fetched on demand
            rather than on every detail-page render. */}
        {open && (
          <PrintLabelDialog
            entityType={entityType}
            entityId={entityId}
            entityName={entityName}
            onClose={() => setOpen(false)}
          />
        )}
      </Modal>
    </>
  );
}

function PrintLabelDialog({
  entityType,
  entityId,
  entityName,
  onClose,
}: {
  entityType: LabelEntityType;
  entityId: string;
  entityName?: string | null;
  onClose: () => void;
}) {
  const templatesQuery = useLabelTemplates(entityType);
  const print = usePrintLabel();
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [copies, setCopies] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);
  const fallback = pickDefaultTemplate(templates, entityType);
  const chosenId = templateId ?? fallback?.id ?? null;

  async function doPrint() {
    if (!chosenId) return;
    setError(null);
    try {
      const job = await print.mutateAsync({
        templateId: chosenId,
        entityId,
        copies,
      });
      toast.success(
        job.code ? `Label printed - code ${job.code}.` : "Label printed.",
      );
      onClose();
    } catch (err) {
      // Deliberately NOT a bare toast-and-close: on the printer-unreachable
      // path the operator needs the job id on screen to reconcile it.
      const message = printErrorMessage(err);
      setError(message);
      toast.error(message);
    }
  }

  return (
    <div className="space-y-3 p-4">
      <div>
        <h3 className="text-base font-semibold">Print label</h3>
        <p className="text-xs text-muted">
          {ENTITY_TYPE_LABELS[entityType]}
          {entityName ? ` - ${entityName}` : ""}
        </p>
      </div>

      <InlineQueryError query={templatesQuery} label="label templates" />

      {!templatesQuery.isLoading && templates.length === 0 ? (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
          No label template exists for this type yet. An admin can create one in
          Settings - Label templates.
        </p>
      ) : (
        <>
          <TemplateField
            templates={templates}
            value={chosenId}
            loading={templatesQuery.isLoading}
            onChange={setTemplateId}
          />
          <CopiesField value={copies} onChange={setCopies} />
        </>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
        >
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={print.isPending || !chosenId}
          onClick={doPrint}
        >
          <Printer size={14} />
          {print.isPending ? "Printing…" : "Print"}
        </button>
      </div>
    </div>
  );
}
