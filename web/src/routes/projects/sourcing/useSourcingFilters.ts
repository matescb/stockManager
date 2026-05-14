import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import type { SourcingWorkspaceSettings } from "./SourceBomButton";
import {
  defaultFromActiveList,
  distributorsFromActiveList,
} from "./sourcingHelpers";
import type { SourcingRequest } from "./sourcingTypes";

export function useSourcingFilters(budgetDisabledUntil: number | null) {
  const [buildQuantity, setBuildQuantity] = useState(1);
  const [country, setCountry] = useState("");
  const [currency, setCurrency] = useState("");
  const [distributors, setDistributors] = useState<string[]>([]);
  const [defaultsApplied, setDefaultsApplied] = useState(false);
  const { data: workspace } = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: ({ signal }) => api.get<SourcingWorkspaceSettings>("/workspaces/current", { signal }),
  });

  useEffect(() => {
    if (!workspace || defaultsApplied) return;
    setCountry(defaultFromActiveList(workspace.sourcing_country_code, workspace.active_countries));
    setCurrency(defaultFromActiveList(workspace.sourcing_currency_code, workspace.active_currencies));
    setDistributors(distributorsFromActiveList(
      workspace.sourcing_preferred_distributors,
      workspace.active_distributors,
    ));
    setDefaultsApplied(true);
  }, [workspace, defaultsApplied]);

  const filterWarnings = useMemo(() => {
    if (!workspace || !defaultsApplied) return [];
    const warnings: string[] = [];
    if (
      workspace.active_countries.length > 0 &&
      workspace.sourcing_country_code &&
      !workspace.active_countries.includes(workspace.sourcing_country_code)
    ) {
      warnings.push(`Workspace default country is not active; using ${workspace.active_countries[0]}.`);
    }
    if (
      workspace.active_currencies.length > 0 &&
      workspace.sourcing_currency_code &&
      !workspace.active_currencies.includes(workspace.sourcing_currency_code)
    ) {
      warnings.push(`Workspace default currency is not active; using ${workspace.active_currencies[0]}.`);
    }
    const preferred = workspace.sourcing_preferred_distributors ?? [];
    if (
      workspace.active_distributors.length > 0 &&
      preferred.length > 0 &&
      preferred.some(item => !workspace.active_distributors.includes(item))
    ) {
      warnings.push("Workspace preferred distributors are not all active; using active distributors only.");
    }
    return warnings;
  }, [workspace, defaultsApplied]);

  const activeListErrors = useMemo(() => {
    if (!workspace) return [];
    const errors: string[] = [];
    if (workspace.active_countries.length === 0) errors.push("No active countries configured.");
    if (workspace.active_currencies.length === 0) errors.push("No active currencies configured.");
    if (workspace.active_distributors.length === 0) errors.push("No active distributors configured.");
    return errors;
  }, [workspace]);

  const requestBody = useMemo<SourcingRequest>(() => {
    const cleanWorkspaceCurrency = workspace?.sourcing_currency_code?.trim().toUpperCase() || null;
    const body: SourcingRequest = {
      build_quantity: Math.max(1, Math.floor(buildQuantity || 1)),
      currency: cleanWorkspaceCurrency,
    };
    const cleanCountry = country.trim().toUpperCase();
    const cleanCurrency = currency.trim().toUpperCase();
    const cleanDistributors = distributors.filter(item => item.trim());
    if (cleanCountry) body.country = cleanCountry;
    if (cleanWorkspaceCurrency && cleanCurrency) body.currency = cleanCurrency;
    if (cleanDistributors.length > 0) body.distributors = cleanDistributors;
    return body;
  }, [buildQuantity, country, currency, distributors, workspace?.sourcing_currency_code]);

  const sourceBlocked =
    buildQuantity < 1 ||
    !defaultsApplied ||
    activeListErrors.length > 0 ||
    (workspace ? !workspace.active_countries.includes(country) : false) ||
    (workspace ? !workspace.active_currencies.includes(currency) : false) ||
    distributors.some(distributor => workspace ? !workspace.active_distributors.includes(distributor) : false) ||
    (budgetDisabledUntil != null && Date.now() < budgetDisabledUntil);

  return {
    activeListErrors,
    buildQuantity,
    country,
    currency,
    distributors,
    filterWarnings,
    requestBody,
    setBuildQuantity,
    setCountry,
    setCurrency,
    setDistributors,
    sourceBlocked,
    workspace,
  };
}
