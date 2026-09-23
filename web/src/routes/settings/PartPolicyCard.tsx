import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { wsKeyOf } from "@/lib/queryKeys";
import { PartCategoriesListSchema } from "@/lib/schemas";
import { InlineQueryError } from "@/components/QueryStateBoundary";

type When = "create" | "complete";

export type PolicyRule = {
  id?: string;
  field: string;
  when: When;
  scope: { category_ids: string[]; include_descendants: boolean; part_types: string[] };
  message: string | null;
  hint: string | null;
};

type PolicyField = {
  field: string;
  label: string;
  kind: "part" | "asset";
  create_allowed: boolean;
  default_hint: string;
};

type PartPolicy = { rules: PolicyRule[]; fields: PolicyField[] };

function blankRule(): PolicyRule {
  return {
    field: "manufacturer",
    when: "create",
    scope: { category_ids: [], include_descendants: true, part_types: [] },
    message: null,
    hint: null,
  };
}

/**
 * Workspace part policy — which fields a part must carry.
 *
 * `create` rules make `POST /parts` (and the MCP `create_part` tool) refuse
 * a part without the field; `complete` rules only report it via
 * `GET /parts/{id}/policy-status`. Editing is admin-only (the PUT is
 * admin-gated server-side); everyone else sees the rules read-only.
 * Contract: docs/api/part-policy.md.
 */
export function PartPolicyCard({ workspaceId, isAdmin }: { workspaceId: string | null; isAdmin: boolean }) {
  const qc = useQueryClient();
  const policyKey = wsKeyOf(workspaceId, "part-policy");
  const policyQuery = useQuery({
    queryKey: policyKey,
    queryFn: ({ signal }) => api.get<PartPolicy>("/part-policy", { signal }),
  });
  const categoriesQuery = useQuery({
    queryKey: wsKeyOf(workspaceId, "categories", { archived: false }),
    queryFn: ({ signal }) => api.parsed.get("/categories", PartCategoriesListSchema, { signal }),
  });
  const [rules, setRules] = useState<PolicyRule[]>([]);

  useEffect(() => {
    if (policyQuery.data) setRules(policyQuery.data.rules);
  }, [policyQuery.data]);

  const saveMutation = useApiMutation<PartPolicy, { rules: PolicyRule[] }>({
    mutationKey: ["part-policy", "save"],
    mutationFn: (body) => api.put<PartPolicy, { rules: PolicyRule[] }>("/part-policy", body),
    onSuccess: (saved) => {
      setRules(saved.rules);
      qc.setQueryData(policyKey, saved);
      toast.success("Part policy saved.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? (e.body?.status?.message ?? e.userMessage) : "Failed");
    },
  });

  const fields = policyQuery.data?.fields ?? [];
  const fieldByName = new Map(fields.map(f => [f.field, f]));
  const categories = categoriesQuery.data ?? [];

  function update(index: number, patch: Partial<PolicyRule>) {
    setRules(rs => rs.map((r, i) => {
      if (i !== index) return r;
      const next = { ...r, ...patch };
      // Asset fields (datasheet, eda.*) can't exist before the part does.
      if (!fieldByName.get(next.field)?.create_allowed) next.when = "complete";
      return next;
    }));
  }

  function setCategory(index: number, categoryId: string) {
    const scope = { ...rules[index].scope, category_ids: categoryId ? [categoryId] : [] };
    update(index, { scope });
  }

  return (
    <div className="card p-4 mb-4 space-y-3 text-sm">
      <h2 className="card-title">Part policy</h2>
      <div className="text-xs text-muted">
        Fields every part must carry. <strong>At create</strong> refuses a new part without the field;{" "}
        <strong>Before complete</strong> lets it be created but reports it as incomplete (datasheets and
        CAD data can only be attached afterwards). Agents read the same rules and hints.
      </div>
      <InlineQueryError query={policyQuery} label="part policy" />
      <InlineQueryError query={categoriesQuery} label="categories" />
      {rules.length === 0 && <div className="text-muted">No rules — any part with a name or MPN is accepted.</div>}
      {rules.map((rule, i) => (
        <div key={rule.id ?? `new-${i}`} className="rounded-md border border-border p-3 space-y-2" data-testid="part-policy-rule">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label className="label" htmlFor={`pp-field-${i}`}>Field</label>
              <select id={`pp-field-${i}`} className="input" disabled={!isAdmin} value={rule.field}
                onChange={e => update(i, { field: e.target.value })}>
                {fields.map(f => <option key={f.field} value={f.field}>{f.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label" htmlFor={`pp-when-${i}`}>Required</label>
              <select id={`pp-when-${i}`} className="input" value={rule.when}
                disabled={!isAdmin || !fieldByName.get(rule.field)?.create_allowed}
                onChange={e => update(i, { when: e.target.value as When })}>
                <option value="create">At create</option>
                <option value="complete">Before complete</option>
              </select>
            </div>
            <div>
              <label className="label" htmlFor={`pp-cat-${i}`}>Category</label>
              <select id={`pp-cat-${i}`} className="input" disabled={!isAdmin}
                value={rule.scope.category_ids[0] ?? ""}
                onChange={e => setCategory(i, e.target.value)}>
                <option value="">All parts</option>
                {categories.map(c => <option key={c.id} value={c.id}>{c.name} (and subcategories)</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="label" htmlFor={`pp-hint-${i}`}>Hint</label>
            <input id={`pp-hint-${i}`} className="input" disabled={!isAdmin} value={rule.hint ?? ""}
              placeholder={fieldByName.get(rule.field)?.default_hint}
              onChange={e => update(i, { hint: e.target.value || null })} />
          </div>
          {isAdmin && (
            <button type="button" className="btn-danger" onClick={() => setRules(rs => rs.filter((_, j) => j !== i))}>
              Remove rule
            </button>
          )}
        </div>
      ))}
      {isAdmin && (
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={() => setRules(rs => [...rs, blankRule()])}>
            Add rule
          </button>
          <button type="button" className="btn-primary" disabled={saveMutation.isPending || policyQuery.isLoading}
            onClick={() => saveMutation.mutate({ rules })}>
            Save part policy
          </button>
        </div>
      )}
    </div>
  );
}
