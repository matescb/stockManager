/**
 * The two form controls the single-label and batch print dialogs share.
 * Extracted so the copy limits (which mirror `TestPrintIn.copies`, 1..20)
 * live in exactly one place.
 */
import { LIMITS, type LabelTemplate } from "./types";

export function TemplateField({
  templates,
  value,
  loading,
  onChange,
}: {
  templates: readonly LabelTemplate[];
  value: string | null;
  loading: boolean;
  onChange: (id: string) => void;
}) {
  return (
    <label className="block">
      <span className="label">Template</span>
      <select
        className="input"
        value={value ?? ""}
        disabled={loading || templates.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {loading && <option value="">Loading…</option>}
        {templates.map((tpl) => (
          <option key={tpl.id} value={tpl.id}>
            {tpl.name}
            {tpl.is_default ? " (default)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

export function CopiesField({
  value,
  onChange,
}: {
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="label">Copies</span>
      <input
        type="number"
        className="input"
        min={1}
        max={LIMITS.MAX_COPIES}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          onChange(
            Number.isFinite(next)
              ? Math.min(LIMITS.MAX_COPIES, Math.max(1, Math.round(next)))
              : 1,
          );
        }}
      />
    </label>
  );
}
