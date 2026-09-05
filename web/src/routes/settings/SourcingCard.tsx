import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { wsKeyOf } from "@/lib/queryKeys";

const LANGUAGE_OPTIONS = [
  { value: "", label: "Default (en)" },
  { value: "de", label: "German (de)" },
  { value: "en", label: "English (en)" },
  { value: "es", label: "Spanish (es)" },
  { value: "fr", label: "French (fr)" },
  { value: "it", label: "Italian (it)" },
  { value: "pt", label: "Portuguese (pt)" },
  { value: "ja", label: "Japanese (ja)" },
  { value: "zh-hans", label: "Chinese, Simplified (zh-hans)" },
  { value: "zh-hant", label: "Chinese, Traditional (zh-hant)" },
] as const;

type SourcingLanguageCode = typeof LANGUAGE_OPTIONS[number]["value"];

export type SourcingWorkspace = {
  id: string;
  sourcing_provider: "none" | "trustedparts";
  sourcing_country_code: string | null;
  sourcing_currency_code: string | null;
  sourcing_language_code: Exclude<SourcingLanguageCode, ""> | null;
  sourcing_preferred_distributors: string[] | null;
  active_countries: string[];
  active_currencies: string[];
  sourcing_use_cached_for_dashboards: boolean;
  has_sourcing_company_id: boolean;
  has_sourcing_api_key: boolean;
};

type SourcingPatch = {
  sourcing_provider: SourcingWorkspace["sourcing_provider"];
  sourcing_country_code: string | null;
  sourcing_currency_code: string | null;
  sourcing_language_code: SourcingWorkspace["sourcing_language_code"];
  sourcing_preferred_distributors: string[];
  sourcing_use_cached_for_dashboards: boolean;
  sourcing_company_id?: string;
  sourcing_api_key?: string;
};

type SourcingTestResult = {
  ok: boolean;
  message: string;
  latency_ms: number;
};

type TestBanner = SourcingTestResult & { text: string };

function splitDistributors(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function testMessage(result: SourcingTestResult): string {
  return `${result.message} (${result.latency_ms} ms)`;
}

export function SourcingCard({
  workspace,
  workspaceId,
}: {
  workspace: SourcingWorkspace;
  workspaceId: string | null | undefined;
}) {
  const qc = useQueryClient();
  const [provider, setProvider] = useState<SourcingWorkspace["sourcing_provider"]>(workspace.sourcing_provider);
  const [companyId, setCompanyId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [companyIdTouched, setCompanyIdTouched] = useState(false);
  const [apiKeyTouched, setApiKeyTouched] = useState(false);
  const [country, setCountry] = useState(workspace.sourcing_country_code ?? "");
  const [currency, setCurrency] = useState(workspace.sourcing_currency_code ?? "");
  const [language, setLanguage] = useState<SourcingLanguageCode>(workspace.sourcing_language_code ?? "");
  const [distributors, setDistributors] = useState(
    (workspace.sourcing_preferred_distributors ?? []).join(", "),
  );
  const [useCache, setUseCache] = useState(workspace.sourcing_use_cached_for_dashboards);
  const [hasCompanyId, setHasCompanyId] = useState(workspace.has_sourcing_company_id);
  const [hasApiKey, setHasApiKey] = useState(workspace.has_sourcing_api_key);
  const [testBanner, setTestBanner] = useState<TestBanner | null>(null);

  useEffect(() => {
    setProvider(workspace.sourcing_provider);
    setCompanyId("");
    setApiKey("");
    setCompanyIdTouched(false);
    setApiKeyTouched(false);
    setCountry(workspace.sourcing_country_code ?? "");
    setCurrency(workspace.sourcing_currency_code ?? "");
    setLanguage(workspace.sourcing_language_code ?? "");
    setDistributors((workspace.sourcing_preferred_distributors ?? []).join(", "));
    setUseCache(workspace.sourcing_use_cached_for_dashboards);
    setHasCompanyId(workspace.has_sourcing_company_id);
    setHasApiKey(workspace.has_sourcing_api_key);
    setTestBanner(null);
  }, [workspace]);

  const saveMutation = useMutation({
    mutationKey: ["workspace", "sourcing", "save"],
    mutationFn: (body: SourcingPatch) =>
      api.patch<SourcingWorkspace, SourcingPatch>("/workspaces/current", body),
    onSuccess: (saved, body) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      setCompanyId("");
      setApiKey("");
      setCompanyIdTouched(false);
      setApiKeyTouched(false);
      setHasCompanyId(saved.has_sourcing_company_id ?? Boolean(body.sourcing_company_id));
      setHasApiKey(saved.has_sourcing_api_key ?? Boolean(body.sourcing_api_key));
      toast.success("Sourcing settings saved.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const testMutation = useMutation({
    mutationKey: ["workspace", "sourcing", "test"],
    mutationFn: () => api.post<SourcingTestResult>("/workspaces/current/sourcing/test", {}),
    onSuccess: (result) => {
      const text = testMessage(result);
      setTestBanner({ ...result, text });
      if (result.ok) {
        toast.success(`Sourcing connection OK: ${text}`);
      } else {
        toast.error(`Sourcing connection failed: ${text}`);
      }
    },
    onError: (e) => {
      const text = e instanceof ApiError ? e.userMessage : "Failed";
      setTestBanner({ ok: false, message: text, latency_ms: 0, text });
      toast.error(text);
    },
  });

  const configured = hasApiKey;
  const activeCountrySelected = Boolean(country && workspace.active_countries.includes(country));
  const activeCurrencySelected = Boolean(currency && workspace.active_currencies.includes(currency));
  const canSave = activeCountrySelected && activeCurrencySelected && !saveMutation.isPending;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!activeCountrySelected || !activeCurrencySelected) return;
    const body: SourcingPatch = {
      sourcing_provider: provider,
      sourcing_country_code: country || null,
      sourcing_currency_code: currency || null,
      sourcing_language_code: language || null,
      sourcing_preferred_distributors: splitDistributors(distributors),
      sourcing_use_cached_for_dashboards: useCache,
    };
    if (companyIdTouched) body.sourcing_company_id = companyId;
    if (apiKeyTouched) body.sourcing_api_key = apiKey;
    saveMutation.mutate(body);
  }

  return (
    <form className="card p-4 mb-4 space-y-3 text-sm" onSubmit={submit}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="card-title">Sourcing provider</h2>
        {configured && (
          <span className="pill bg-success/10 text-success" aria-label="Sourcing credentials configured">
            Configured ✓
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="sourcing-provider">Provider</label>
          <select
            id="sourcing-provider"
            className="input"
            value={provider}
            onChange={(e) => setProvider(e.target.value as SourcingWorkspace["sourcing_provider"])}
          >
            <option value="none">None</option>
            <option value="trustedparts">TrustedParts</option>
          </select>
        </div>
        <div>
          <label
            className="label"
            htmlFor="sourcing-company-id"
            title="TrustedParts no longer requires this; you may safely leave it blank"
          >
            CompanyId (deprecated)
          </label>
          <input
            id="sourcing-company-id"
            className="input font-mono text-xs"
            type="password"
            autoComplete="off"
            value={companyId}
            onChange={(e) => {
              setCompanyIdTouched(true);
              setCompanyId(e.target.value);
            }}
            placeholder={hasCompanyId ? "•••••••• (deprecated CompanyId set)" : "Optional"}
            title="TrustedParts no longer requires this; you may safely leave it blank"
          />
        </div>
        <div>
          <label className="label" htmlFor="sourcing-api-key">API Key</label>
          <input
            id="sourcing-api-key"
            className="input font-mono text-xs"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => {
              setApiKeyTouched(true);
              setApiKey(e.target.value);
            }}
            placeholder={hasApiKey ? "•••••••• (API key set)" : "API key"}
          />
        </div>
        <div>
          <label className="label" htmlFor="sourcing-country">Country</label>
          <select
            id="sourcing-country"
            className="input uppercase"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
          >
            <option value="">Select country</option>
            {workspace.active_countries.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="sourcing-currency">Currency</label>
          <select
            id="sourcing-currency"
            className="input uppercase"
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          >
            <option value="">Select currency</option>
            {workspace.active_currencies.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="sourcing-language">Language</label>
          <select
            id="sourcing-language"
            className="input"
            value={language}
            onChange={(e) => setLanguage(e.target.value as SourcingLanguageCode)}
          >
            {LANGUAGE_OPTIONS.map((option) => (
              <option key={option.value || "default"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="sourcing-distributors">Preferred distributors</label>
          <input
            id="sourcing-distributors"
            className="input"
            value={distributors}
            onChange={(e) => setDistributors(e.target.value)}
            placeholder="DigiKey, Mouser"
          />
        </div>
      </div>

      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={useCache}
          onChange={(e) => setUseCache(e.target.checked)}
        />
        Use cached data for dashboards
      </label>

      {testBanner && (
        <div
          role={testBanner.ok ? "status" : "alert"}
          className={testBanner.ok ? "text-xs text-success" : "text-xs text-danger"}
        >
          {testBanner.text}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" type="submit" disabled={!canSave}>
          Save
        </button>
        <button
          className="btn"
          type="button"
          disabled={testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          Test connection
        </button>
      </div>
    </form>
  );
}
