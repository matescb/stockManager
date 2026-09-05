/**
 * Data layer for the label designer and the print actions.
 *
 * Every call goes through `lib/api.ts` (so the session cookie rides along and
 * `ApiError` is uniform) and every cache key keeps the `["ws", workspaceId, …]`
 * prefix required by `lib/queryKeys.ts`.
 *
 * REST surface — `backend/app/api/routes/label_templates.py`:
 *
 *   GET    /label-templates[?entity_type=]   list          (member)
 *   POST   /label-templates                  create        (admin)
 *   POST   /label-templates/defaults         seed built-ins(admin, idempotent)
 *   PATCH  /label-templates/{id}             update        (admin)
 *   DELETE /label-templates/{id}             delete        (admin)
 *   GET    /label-templates/{id}/jscript     debug render  (member)
 *   POST   /label-templates/{id}/test-print  print         (admin, 20/min)
 *
 * `test-print` is also the object-print path: with an `entity_id` in the body
 * it renders that object's real data and mints its short code. There is no
 * second "print this part" endpoint and this PR does not add one.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useAuth } from "@/lib/auth";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { toCreatePayload, toUpdatePayload } from "./factory";
import {
  RenderSchema,
  TemplateListSchema,
  TemplateSchema,
  TestPrintSchema,
  type LabelEntityType,
  type LabelTemplate,
  type TemplateDraft,
  type TestPrintResult,
} from "./types";

const BASE = "/label-templates";

/** Cache-key tail. Always used behind `useWsKey` / `wsKeyOf`. */
export const LABEL_TEMPLATES_KEY = "label-templates";

// ---------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------

/**
 * Every template in the workspace, or just one entity type's.
 *
 * The whole list is small (a handful of rows per workspace) and the designer
 * flips between entity tabs constantly, so the unfiltered call is the default
 * and the tabs filter client-side — one cache entry instead of five.
 */
export function useLabelTemplates(entity?: LabelEntityType) {
  const path = entity ? `${BASE}?entity_type=${encodeURIComponent(entity)}` : BASE;
  return useQuery({
    queryKey: useWsKey(LABEL_TEMPLATES_KEY, entity ?? "all"),
    queryFn: ({ signal }) => api.parsed.get(path, TemplateListSchema, { signal }),
  });
}

/**
 * The rendered JScript for a saved template against SAMPLE data — the
 * server's own debug view of "what will actually be sent to the printer".
 * Disabled for an unsaved draft, which has no id to render.
 */
export function useTemplateJscript(templateId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: useWsKey(LABEL_TEMPLATES_KEY, templateId ?? "none", "jscript"),
    enabled: enabled && templateId != null,
    queryFn: ({ signal }) =>
      api.parsed.get(`${BASE}/${templateId}/jscript`, RenderSchema, { signal }),
  });
}

/**
 * Pick the template a print action should default to: the entity type's
 * default, else the first one it has, else null.
 */
export function pickDefaultTemplate(
  templates: readonly LabelTemplate[],
  entity: LabelEntityType,
): LabelTemplate | null {
  const forEntity = templates.filter((t) => t.entity_type === entity);
  return forEntity.find((t) => t.is_default) ?? forEntity[0] ?? null;
}

// ---------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------

/** Invalidate by key PREFIX so both the "all" and the per-entity lists refresh. */
function useInvalidateTemplates() {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  return () => {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, LABEL_TEMPLATES_KEY) });
  };
}

export function useCreateTemplate() {
  const invalidate = useInvalidateTemplates();
  return useApiMutation<LabelTemplate, TemplateDraft>({
    mutationKey: [LABEL_TEMPLATES_KEY, "create"],
    mutationFn: (draft) =>
      api.parsed.post(BASE, TemplateSchema, toCreatePayload(draft)),
    onSuccess: invalidate,
  });
}

export function useUpdateTemplate() {
  const invalidate = useInvalidateTemplates();
  return useApiMutation<LabelTemplate, { id: string; draft: TemplateDraft }>({
    mutationKey: [LABEL_TEMPLATES_KEY, "update"],
    mutationFn: ({ id, draft }) =>
      api.parsed.patch(`${BASE}/${id}`, TemplateSchema, toUpdatePayload(draft)),
    onSuccess: invalidate,
  });
}

export function useDeleteTemplate() {
  const invalidate = useInvalidateTemplates();
  return useApiMutation<unknown, { id: string }>({
    mutationKey: [LABEL_TEMPLATES_KEY, "delete"],
    mutationFn: ({ id }) => api.delete(`${BASE}/${id}`),
    onSuccess: invalidate,
  });
}

/** Promote one template to its entity type's default (server demotes the incumbent). */
export function useSetDefaultTemplate() {
  const invalidate = useInvalidateTemplates();
  return useApiMutation<LabelTemplate, { id: string }>({
    mutationKey: [LABEL_TEMPLATES_KEY, "set-default"],
    mutationFn: ({ id }) =>
      api.parsed.patch(`${BASE}/${id}`, TemplateSchema, { is_default: true }),
    onSuccess: invalidate,
  });
}

/** Materialise the built-in default per entity type. Idempotent server-side. */
export function useSeedDefaultTemplates() {
  const invalidate = useInvalidateTemplates();
  return useApiMutation<LabelTemplate[], void>({
    mutationKey: [LABEL_TEMPLATES_KEY, "seed-defaults"],
    mutationFn: () => api.parsed.post(`${BASE}/defaults`, TemplateListSchema, {}),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------
// Printing
// ---------------------------------------------------------------------

export type PrintRequest = {
  templateId: string;
  /** Omit to print the template against sample data (a layout test print). */
  entityId?: string;
  copies?: number;
};

/**
 * Send one label to the printer.
 *
 * No cache invalidation: printing mutates the `print_jobs` ledger, which this
 * frontend does not read (there is no print-jobs route yet), and it mints an
 * object code, which nothing here caches either.
 */
export function usePrintLabel() {
  return useApiMutation<TestPrintResult, PrintRequest>({
    mutationKey: [LABEL_TEMPLATES_KEY, "print"],
    mutationFn: ({ templateId, entityId, copies }) =>
      api.parsed.post(`${BASE}/${templateId}/test-print`, TestPrintSchema, {
        ...(entityId ? { entity_id: entityId } : {}),
        copies: copies ?? 1,
      }),
  });
}

// ---------------------------------------------------------------------
// Error surfacing
// ---------------------------------------------------------------------

/** The `print_job_id` the 409 spreads onto the error body, when present. */
export function printJobIdOf(error: unknown): string | null {
  if (!(error instanceof ApiError) || !error.body) return null;
  const id = (error.body as Record<string, unknown>).print_job_id;
  return typeof id === "string" ? id : null;
}

/** True when the failure is "the printer could not be reached". */
export function isPrinterUnreachable(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.code === "printer.unreachable"
  );
}

/**
 * A message an operator can act on.
 *
 * The case that matters most is the one production is in right now: with
 * `PRINT_HOST` empty the backend fails CLOSED — it records a `print_jobs` row,
 * marks it `failed` and answers 409 `printer.unreachable` (never a 500, and
 * never a silent success). The UI has to say exactly that: nothing came out of
 * the printer, but the attempt is on record. See
 * `docs/deployment.md` — "Label printer connectivity".
 */
export function printErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Print failed. Please try again.";
  }
  if (isPrinterUnreachable(error)) {
    const jobId = printJobIdOf(error);
    return (
      "Printer not configured or unreachable — nothing was printed. " +
      "The attempt was recorded as a failed print job" +
      (jobId ? ` (${jobId})` : "") +
      " so it can be reconciled."
    );
  }
  switch (error.status) {
    case 403:
      return "Printing labels needs an admin role in this workspace.";
    case 404:
      return "That label template no longer exists — reload and pick another.";
    case 429:
      return "Too many print requests. Wait a minute and try again.";
    default:
      return error.userMessage;
  }
}
