/**
 * Batch print for a multi-select list.
 *
 * Ported in shape from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/BatchPrintDialog.tsx`),
 * but the mechanism is different and deliberately so: skladVA has a server
 * `print-batch` endpoint that allocates N blank codes in one call. This
 * codebase has no batch endpoint — labels here are printed FOR EXISTING
 * OBJECTS, one `test-print` call each, so the dialog drives the loop client
 * side and reports per-object progress.
 *
 * Two consequences the UI has to own:
 *  - `test-print` is rate-limited to 20/minute per workspace, so the selection
 *    is capped at `MAX_BATCH` rather than firing 200 requests into a 429.
 *  - The run STOPS at the first failure. With `PRINT_HOST` unset every call
 *    fails identically; continuing would queue N failed print jobs and bury
 *    the one message that matters.
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

/** Matches the server-side `20/minute` limit on `test-print`. */
export const MAX_BATCH = 20;

export interface BatchPrintItem {
  id: string;
  label: string;
}

interface BatchPrintDialogProps {
  open: boolean;
  entityType: LabelEntityType;
  items: readonly BatchPrintItem[];
  onClose: () => void;
  /** Called after a fully successful run, so the list can clear its selection. */
  onDone?: () => void;
}

export default function BatchPrintDialog(props: BatchPrintDialogProps) {
  return (
    <Modal open={props.open} onClose={props.onClose} title="Print labels" size="sm">
      {props.open && <BatchPrintBody {...props} />}
    </Modal>
  );
}

function BatchPrintBody({
  entityType,
  items,
  onClose,
  onDone,
}: BatchPrintDialogProps) {
  const templatesQuery = useLabelTemplates(entityType);
  const print = usePrintLabel();
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [copies, setCopies] = useState(1);
  const [done, setDone] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);
  const fallback = pickDefaultTemplate(templates, entityType);
  const chosenId = templateId ?? fallback?.id ?? null;
  const overLimit = items.length > MAX_BATCH;
  const batch = items.slice(0, MAX_BATCH);

  async function run() {
    if (!chosenId || running) return;
    setRunning(true);
    setError(null);
    setFinished(false);
    setDone(0);
    let printed = 0;
    for (const item of batch) {
      try {
        // Sequential on purpose: the printer is a single physical device and
        // the rate limit is per workspace, so parallelism buys nothing and
        // scrambles the order labels come out in.
        await print.mutateAsync({ templateId: chosenId, entityId: item.id, copies });
        printed += 1;
        setDone(printed);
      } catch (err) {
        const message = printErrorMessage(err);
        setError(
          printed === 0
            ? message
            : `Stopped after ${printed} of ${batch.length} labels. ${message}`,
        );
        toast.error(message);
        setRunning(false);
        return;
      }
    }
    setRunning(false);
    setFinished(true);
    toast.success(`Printed ${printed} label${printed === 1 ? "" : "s"}.`);
    onDone?.();
  }

  return (
    <div className="space-y-3 p-4">
      <div>
        <h3 className="text-base font-semibold">Print labels</h3>
        <p className="text-xs text-muted">
          {items.length} {ENTITY_TYPE_LABELS[entityType].toLowerCase()}
          {items.length === 1 ? "" : "s"} selected
        </p>
      </div>

      <InlineQueryError query={templatesQuery} label="label templates" />

      {overLimit && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          Printing is rate-limited to {MAX_BATCH} labels a minute, so only the
          first {MAX_BATCH} of your selection will be printed.
        </p>
      )}

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

      {(running || done > 0) && (
        <p className="text-sm text-muted" aria-live="polite">
          Printed {done} of {batch.length}…
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
        >
          {error}
        </p>
      )}

      {finished && !error && (
        <p className="text-sm text-success">All {done} labels sent to the printer.</p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button type="button" className="btn" onClick={onClose} disabled={running}>
          Close
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={running || !chosenId || batch.length === 0}
          onClick={run}
        >
          <Printer size={14} />
          {running ? "Printing…" : `Print ${batch.length * copies} label${batch.length * copies === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}
