import type { Dispatch, SetStateAction } from "react";
import { BellPlus, RefreshCw } from "lucide-react";
import type { SourcingWorkspaceSettings } from "./SourceBomButton";

type Props = {
  activeListErrors: string[];
  buildQuantity: number;
  country: string;
  currency: string;
  distributors: string[];
  filterWarnings: string[];
  hasRows: boolean;
  isSourcing: boolean;
  projectId: string;
  sourceDisabled: boolean;
  workspace?: SourcingWorkspaceSettings;
  onAlertOpen: () => void;
  onBuildQuantityChange: (value: number) => void;
  onCountryChange: (value: string) => void;
  onCurrencyChange: (value: string) => void;
  onDistributorsChange: Dispatch<SetStateAction<string[]>>;
  onPlanOpen: () => void;
  onSource: () => void;
};

export function SourcingControls({
  activeListErrors,
  buildQuantity,
  country,
  currency,
  distributors,
  filterWarnings,
  hasRows,
  isSourcing,
  projectId,
  sourceDisabled,
  workspace,
  onAlertOpen,
  onBuildQuantityChange,
  onCountryChange,
  onCurrencyChange,
  onDistributorsChange,
  onPlanOpen,
  onSource,
}: Props) {
  return (
    <div className="card p-4">
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
        <div>
          <label className="label" htmlFor="sourcing-build-quantity">Build quantity</label>
          <input
            id="sourcing-build-quantity"
            className="input"
            type="number"
            min={1}
            step={1}
            value={buildQuantity}
            onChange={event => onBuildQuantityChange(Number(event.target.value))}
          />
        </div>
        <div>
          <label className="label" htmlFor="sourcing-country">Country</label>
          <select
            id="sourcing-country"
            className="input uppercase"
            value={country}
            onChange={event => onCountryChange(event.target.value)}
            disabled={(workspace?.active_countries.length ?? 0) === 0}
          >
            {(workspace?.active_countries ?? []).map(item => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="sourcing-currency">Currency</label>
          <select
            id="sourcing-currency"
            className="input uppercase"
            value={currency}
            onChange={event => onCurrencyChange(event.target.value)}
            disabled={(workspace?.active_currencies.length ?? 0) === 0}
          >
            {(workspace?.active_currencies ?? []).map(item => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>
        <fieldset className="md:col-span-2">
          <legend className="label">Distributors</legend>
          <div className="max-h-52 overflow-auto rounded border border-border p-2">
            {(workspace?.active_distributors ?? []).map(item => (
              <label key={item} className="flex items-center gap-2 py-1 text-sm">
                <input
                  type="checkbox"
                  checked={distributors.includes(item)}
                  onChange={event => {
                    onDistributorsChange(current => event.target.checked
                      ? [...current, item]
                      : current.filter(distributor => distributor !== item));
                  }}
                />
                <span>{item}</span>
              </label>
            ))}
            {(workspace?.active_distributors.length ?? 0) === 0 && (
              <div className="text-xs text-muted py-1">No active distributors configured.</div>
            )}
          </div>
        </fieldset>
      </div>
      {activeListErrors.length > 0 && (
        <div className="mt-3 text-xs text-muted" role="status">
          {activeListErrors.join(" ")} Open Settings → Workspace to update active lists.
        </div>
      )}
      {filterWarnings.length > 0 && (
        <div className="mt-3 text-xs text-warning" role="status">
          {filterWarnings.join(" ")}
        </div>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          className="btn"
          disabled={!projectId}
          onClick={onAlertOpen}
        >
          <BellPlus size={14} aria-hidden="true" />
          Set BOM-buyable alert
        </button>
        <button
          type="button"
          className="btn"
          disabled={sourceDisabled || !hasRows}
          onClick={onPlanOpen}
        >
          Generate purchase plan
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={sourceDisabled}
          onClick={onSource}
        >
          <RefreshCw size={14} className={isSourcing ? "animate-spin" : ""} />
          {isSourcing ? "Sourcing…" : "Source"}
        </button>
      </div>
    </div>
  );
}
