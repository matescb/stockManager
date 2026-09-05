/**
 * Printable pick-list route (Track B4).
 *
 * Mounted at `/builds/:buildId/pick-list` and
 * `/builds/:buildId/stages/:stageId/pick-list`; reachable from one button
 * on the build detail page.
 *
 * The stage picker lives here rather than as a second button on the build
 * page: a staged build's operator wants one stage's parts, and switching
 * between sheets is a decision you make holding the paper, not before you
 * open it. It is on-screen chrome and never prints.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import PickListSheet from "./PickListSheet";
import { NO_PRINT_CLASS } from "./printStyles";
import type { PickList, PickListStage } from "./types";

type StageSummary = PickListStage;

export default function PickListView() {
  const { buildId, stageId } = useParams<{ buildId: string; stageId?: string }>();

  if (!buildId) {
    return <div className="text-danger text-sm p-4">Missing build id.</div>;
  }

  return <PickListQuery key={`${buildId}:${stageId ?? ""}`} buildId={buildId} stageId={stageId} />;
}

function PickListQuery({ buildId, stageId }: { buildId: string; stageId?: string }) {
  const path = stageId
    ? `/builds/${buildId}/stages/${stageId}/pick-list`
    : `/builds/${buildId}/pick-list`;

  const { data, isError, error } = useQuery({
    queryKey: useWsKey("build", buildId, `pick-list:${stageId ?? "all"}`),
    queryFn: ({ signal }) => api.get<PickList>(path, { signal }),
  });

  // Stages drive the picker. A single-pass build returns [] and the picker
  // stays hidden — no extra chrome on the sheet that needs it least.
  const { data: stages } = useQuery({
    queryKey: useWsKey("build", buildId, "stages"),
    queryFn: ({ signal }) => api.get<StageSummary[]>(`/builds/${buildId}/stages`, { signal }),
  });

  if (isError) {
    return (
      <div className="text-danger text-sm p-4">
        Failed to load pick list.{" "}
        {error instanceof ApiError ? error.userMessage : ""}
      </div>
    );
  }
  if (!data) return <div className="text-muted p-4">Loading…</div>;

  return (
    <PickListSheet
      data={data}
      controls={
        <PickListControls
          buildId={buildId}
          stageId={stageId}
          stages={stages ?? []}
        />
      }
    />
  );
}

function PickListControls({
  buildId,
  stageId,
  stages,
}: {
  buildId: string;
  stageId?: string;
  stages: StageSummary[];
}) {
  const nav = useNavigate();

  function goToStage(next: string) {
    nav(
      next
        ? `/builds/${buildId}/stages/${next}/pick-list`
        : `/builds/${buildId}/pick-list`,
    );
  }

  return (
    <div className={`${NO_PRINT_CLASS} mb-4 flex flex-wrap items-end gap-3`}>
      <Link className="btn" to={`/builds/${buildId}`}>
        ← Back to build
      </Link>

      {stages.length > 0 && (
        <div>
          <label className="label" htmlFor="pick-list-stage">
            Stage
          </label>
          <select
            id="pick-list-stage"
            className="input"
            value={stageId ?? ""}
            onChange={e => goToStage(e.target.value)}
          >
            <option value="">Whole build</option>
            {stages.map(stage => (
              <option key={stage.id} value={stage.id}>
                {stage.sequence + 1}. {stage.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <button
        type="button"
        className="btn-primary ml-auto"
        onClick={() => window.print()}
      >
        Print
      </button>
    </div>
  );
}
