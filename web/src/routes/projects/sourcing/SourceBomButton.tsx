import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";

export type SourcingWorkspaceSettings = {
  sourcing_country_code: string | null;
  sourcing_currency_code: string | null;
  sourcing_preferred_distributors: string[] | null;
  active_countries: string[];
  active_currencies: string[];
  active_distributors: string[];
  has_sourcing_api_key: boolean;
};

type Props = {
  projectId: string | null | undefined;
  className?: string;
};

export function SourceBomButton({ projectId, className }: Props) {
  const { data: workspace } = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: () => api.get<SourcingWorkspaceSettings>("/workspaces/current"),
  });
  const disabled = workspace?.has_sourcing_api_key === false;
  const classes = className ?? "btn-primary";

  if (!projectId || disabled) {
    return (
      <span className="inline-flex flex-col items-end gap-1">
        <button
          type="button"
          className={classes}
          disabled
          title="Sourcing not configured"
        >
          Source BOM
        </button>
        {disabled && (
          <span className="text-xs text-muted">Sourcing not configured</span>
        )}
      </span>
    );
  }

  return (
    <Link className={classes} to={`/projects/${projectId}/sourcing`}>
      Source BOM
    </Link>
  );
}
