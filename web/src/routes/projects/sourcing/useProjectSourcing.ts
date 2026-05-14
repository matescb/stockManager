import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { errorStatus, sourcingErrorToastMessage } from "./sourcingHelpers";
import type { SourcingBomResponse, SourcingRequest } from "./sourcingTypes";

const SOURCING_BOM_GC_TIME_MS = 30 * 60 * 1000;

export function useProjectSourcing({
  projectId,
  onBudgetPaused,
}: {
  projectId?: string;
  onBudgetPaused: (disabledUntil: number) => void;
}) {
  const queryClient = useQueryClient();
  const sourcingDisplayCacheKey = useWsKey("project-sourcing", projectId);
  const cachedSourcing = useQuery<SourcingBomResponse | null>({
    queryKey: sourcingDisplayCacheKey,
    queryFn: async ({ signal: _signal }) => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: SOURCING_BOM_GC_TIME_MS,
  });
  const sourcing = useMutation<SourcingBomResponse, unknown, SourcingRequest>({
    mutationFn: body =>
      api.post<SourcingBomResponse, SourcingRequest>(`/projects/${projectId}/sourcing`, body),
    onSuccess: result => {
      queryClient.setQueryData(sourcingDisplayCacheKey, result);
    },
    onError: error => {
      const status = errorStatus(error);
      if (status === 503) onBudgetPaused(Date.now() + 5 * 60 * 1000);
      toast.error(sourcingErrorToastMessage(error));
    },
  });

  return {
    sourcing,
    sourcingData: sourcing.data ?? cachedSourcing.data ?? undefined,
  };
}
