import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { wsKeyOf } from "@/lib/queryKeys";
import { InlineQueryError } from "@/components/QueryStateBoundary";

export type ActiveListsWorkspace = {
  id: string;
  active_currencies: string[];
  active_countries: string[];
  active_distributors: string[];
};

type MasterLists = {
  currencies: string[];
  countries: string[];
  distributors: string[];
};

type ActiveListsPatch = {
  active_currencies: string[];
  active_countries: string[];
  active_distributors: string[];
};

type FieldKey = keyof ActiveListsPatch;
type FieldErrors = Partial<Record<FieldKey, string>>;

const FIELD_LABELS: Record<FieldKey, string> = {
  active_currencies: "currency",
  active_countries: "country",
  active_distributors: "distributor",
};

function mergeOptions(master: string[], selected: string[]): string[] {
  return [...master, ...selected.filter((item) => !master.includes(item))];
}

function fieldFromError(path: string): FieldKey | null {
  if (path.includes("active_currencies")) return "active_currencies";
  if (path.includes("active_countries")) return "active_countries";
  if (path.includes("active_distributors")) return "active_distributors";
  return null;
}

function errorsFromApi(error: ApiError): FieldErrors {
  const next: FieldErrors = {};
  for (const item of error.body?.errors ?? []) {
    const field = fieldFromError(item.field);
    if (field && !next[field]) next[field] = item.message;
  }
  return next;
}

function validate(body: ActiveListsPatch): FieldErrors {
  const next: FieldErrors = {};
  for (const [field, values] of Object.entries(body) as [FieldKey, string[]][]) {
    if (values.length === 0) {
      next[field] = `Select at least one ${FIELD_LABELS[field]}.`;
    }
  }
  return next;
}

function CheckboxList({
  title,
  searchLabel,
  values,
  selected,
  search,
  onSearch,
  onToggle,
  error,
}: {
  title: string;
  searchLabel: string;
  values: string[];
  selected: string[];
  search: string;
  onSearch: (value: string) => void;
  onToggle: (value: string, checked: boolean) => void;
  error?: string;
}) {
  const needle = search.trim().toLowerCase();
  const visible = needle
    ? values.filter((value) => value.toLowerCase().includes(needle))
    : values;

  return (
    <fieldset className="space-y-2">
      <legend className="font-medium">{title}</legend>
      <label className="label" htmlFor={`${searchLabel}-search`}>
        Search {searchLabel}
      </label>
      <input
        id={`${searchLabel}-search`}
        className="input"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
      />
      <div className="max-h-52 overflow-auto rounded border border-border p-2">
        {visible.map((value) => (
          <label key={value} className="flex items-center gap-2 py-1">
            <input
              type="checkbox"
              checked={selected.includes(value)}
              onChange={(event) => onToggle(value, event.target.checked)}
            />
            <span>{value}</span>
          </label>
        ))}
        {visible.length === 0 && <div className="text-xs text-muted py-1">No matches</div>}
      </div>
      {error && <div className="text-xs text-danger">{error}</div>}
    </fieldset>
  );
}

export function ActiveListsCard({
  workspace,
  workspaceId,
}: {
  workspace: ActiveListsWorkspace;
  workspaceId: string | null | undefined;
}) {
  const qc = useQueryClient();
  const [currencies, setCurrencies] = useState(workspace.active_currencies);
  const [countries, setCountries] = useState(workspace.active_countries);
  const [distributors, setDistributors] = useState(workspace.active_distributors);
  const [currencySearch, setCurrencySearch] = useState("");
  const [countrySearch, setCountrySearch] = useState("");
  const [distributorSearch, setDistributorSearch] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});

  useEffect(() => {
    setCurrencies(workspace.active_currencies);
    setCountries(workspace.active_countries);
    setDistributors(workspace.active_distributors);
    setErrors({});
  }, [workspace]);

  const masterQuery = useQuery({
    queryKey: ["workspaces", "master-lists"],
    queryFn: () => api.get<MasterLists>("/workspaces/master-lists"),
  });

  const options = useMemo(() => {
    const lists = masterQuery.data ?? { currencies: [], countries: [], distributors: [] };
    return {
      currencies: mergeOptions(lists.currencies, currencies),
      countries: mergeOptions(lists.countries, countries),
      distributors: mergeOptions(lists.distributors, distributors),
    };
  }, [countries, currencies, distributors, masterQuery.data]);

  const saveMutation = useMutation({
    mutationKey: ["workspace", "active-lists", "save"],
    mutationFn: (body: ActiveListsPatch) =>
      api.patch<ActiveListsWorkspace, ActiveListsPatch>("/workspaces/current", body),
    onSuccess: (saved) => {
      setCurrencies(saved.active_currencies);
      setCountries(saved.active_countries);
      setDistributors(saved.active_distributors);
      setErrors({});
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      toast.success("Active lists saved.");
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setErrors(errorsFromApi(error));
        toast.error(error.userMessage);
      } else {
        toast.error("Failed");
      }
    },
  });

  function toggle(setter: (value: string[]) => void, selected: string[], value: string, checked: boolean) {
    setter(checked ? [...selected, value] : selected.filter((item) => item !== value));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = {
      active_currencies: currencies,
      active_countries: countries,
      active_distributors: distributors,
    };
    const nextErrors = validate(body);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    saveMutation.mutate(body);
  }

  return (
    <form className="card p-4 mb-4 space-y-4 text-sm" onSubmit={submit}>
      <h2 className="text-md font-semibold">Active currencies / countries / distributors</h2>
      <InlineQueryError query={masterQuery} label="workspace master lists" />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <CheckboxList
          title="Active currencies"
          searchLabel="currencies"
          values={options.currencies}
          selected={currencies}
          search={currencySearch}
          onSearch={setCurrencySearch}
          onToggle={(value, checked) => toggle(setCurrencies, currencies, value, checked)}
          error={errors.active_currencies}
        />
        <CheckboxList
          title="Active countries"
          searchLabel="countries"
          values={options.countries}
          selected={countries}
          search={countrySearch}
          onSearch={setCountrySearch}
          onToggle={(value, checked) => toggle(setCountries, countries, value, checked)}
          error={errors.active_countries}
        />
        <CheckboxList
          title="Active distributors"
          searchLabel="distributors"
          values={options.distributors}
          selected={distributors}
          search={distributorSearch}
          onSearch={setDistributorSearch}
          onToggle={(value, checked) => toggle(setDistributors, distributors, value, checked)}
          error={errors.active_distributors}
        />
      </div>
      <button
        className="btn-primary"
        type="submit"
        disabled={saveMutation.isPending || masterQuery.isLoading}
      >
        Save active lists
      </button>
    </form>
  );
}
