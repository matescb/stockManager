/**
 * SM-010 — per-file review state on the part's CAD tab.
 *
 * One row per hosted CAD file (symbol, footprint, first 3D model, SPICE
 * model) with its review state and what agents recorded about it (origin,
 * model kind, simulators, validity). An owner/admin signed in to the app
 * gets "Mark reviewed" / "Reject" with a "checked against" note; the
 * server refuses every API token and every lesser role regardless
 * (`eda.review_requires_owner`), so `can_review` only hides controls.
 *
 * The review is pinned to the bytes on screen: the PUT carries the
 * `sha256` shown, and a file replaced meanwhile comes back as a 409.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { PartEdaAssetsSchema, type EdaAssetEntry } from "@/lib/schemas";
import { InlineQueryError } from "@/components/QueryStateBoundary";

const KINDS = [
  ["symbol", "Symbol"],
  ["footprint", "Footprint"],
  ["model3d", "3D model"],
  ["spice", "SPICE model"],
] as const;

type Kind = (typeof KINDS)[number][0];
type Verdict = "reviewed" | "rejected";

const STATE_CLASS: Record<EdaAssetEntry["review"]["state"], string> = {
  unreviewed: "pill",
  reviewed: "pill bg-success/15 text-success",
  rejected: "pill bg-danger/15 text-danger",
};

function describe(asset: EdaAssetEntry): string {
  return [
    asset.origin && `from ${asset.origin}`,
    asset.model_kind,
    asset.simulators.length > 0 && `checked with ${asset.simulators.join(", ")}`,
    asset.valid_for.length > 0 && `valid for ${asset.valid_for.join(", ")}`,
    asset.not_valid_for.length > 0 && `not for ${asset.not_valid_for.join(", ")}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function PartCadAssetReview({ partId }: { partId: string }) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const query = useQuery({
    queryKey: useWsKey("part", partId, "eda", "assets"),
    queryFn: ({ signal }) =>
      api.parsed.get(`/parts/${partId}/eda/assets`, PartEdaAssetsSchema, { signal }),
    enabled: Boolean(partId),
  });
  const [against, setAgainst] = useState<Partial<Record<Kind, string>>>({});
  const [err, setErr] = useState<string | null>(null);

  const review = useApiMutation<unknown, { kind: Kind; state: Verdict; sha256: string }>({
    mutationKey: ["part", partId, "eda", "review"],
    mutationFn: ({ kind, state, sha256 }) =>
      api.put(`/parts/${partId}/eda/${kind}/review`, {
        state,
        sha256,
        against: against[kind]?.trim() || null,
      }),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "eda", "assets") });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "eda.asset_changed") {
        setErr("The file was replaced since this page loaded; review the new version.");
        qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "eda", "assets") });
        return;
      }
      setErr(e instanceof ApiError ? e.userMessage : "Failed to record the review");
    },
  });

  const data = query.data;
  const present = KINDS.filter(([kind]) => data?.assets[kind]);
  if (query.isSuccess && present.length === 0) return null;

  return (
    <section className="card p-4 space-y-3" aria-label="File review">
      <h3 className="card-title">File review</h3>
      <InlineQueryError query={query} label="file review" />
      {err && <div className="text-danger text-sm">{err}</div>}
      <ul className="space-y-3">
        {present.map(([kind, label]) => {
          const asset = data!.assets[kind]!;
          const facts = describe(asset);
          return (
            <li key={kind} className="space-y-1" data-testid={`asset-review-${kind}`}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{label}</span>
                <span className="text-muted truncate">{asset.name}</span>
                <code className="text-xs text-muted" title={asset.sha256}>
                  {asset.sha256.slice(0, 10)}
                </code>
                <span className={STATE_CLASS[asset.review.state]}>{asset.review.state}</span>
              </div>
              {asset.review.state !== "unreviewed" && (
                <div className="text-xs text-muted">
                  {asset.review.by_name ?? "someone"}, {formatDate(asset.review.at)}
                  {asset.review.against && ` — checked against ${asset.review.against}`}
                </div>
              )}
              {facts && <div className="text-xs text-muted">{facts}</div>}
              {data!.can_review && (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    className="input flex-1 min-w-48"
                    aria-label={`${label} checked against`}
                    placeholder="Checked against, e.g. datasheet rev C fig 12"
                    maxLength={500}
                    value={against[kind] ?? ""}
                    onChange={(e) => setAgainst((prev) => ({ ...prev, [kind]: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ kind, state: "reviewed", sha256: asset.sha256 })}
                  >
                    Mark reviewed
                  </button>
                  <button
                    type="button"
                    className="btn-danger btn-sm"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ kind, state: "rejected", sha256: asset.sha256 })}
                  >
                    Reject
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
