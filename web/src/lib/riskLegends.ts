import type { RiskTone } from "./sourcing";

export type LegendRow = {
  label: string;
  tone: RiskTone;
  description: string;
};

export const LIFECYCLE_LEGEND: LegendRow[] = [
  { label: "Low", tone: "good", description: "This product is active." },
  {
    label: "Low-Med",
    tone: "low-warning",
    description: "This product may be a special order, NRND (not recommended for new design), or a known equivalent.",
  },
  { label: "Med", tone: "warning", description: "This product may be EOL (end of life) or NRND." },
  { label: "High", tone: "danger", description: "This product is end of life." },
];

export const SUPPLY_CHAIN_LEGEND: LegendRow[] = [
  { label: "Low", tone: "good", description: "Available stock with short lead times." },
  { label: "Low-Med", tone: "low-warning", description: "Limited stock or long lead times." },
  { label: "Med", tone: "warning", description: "Out of stock with short lead times." },
  { label: "High", tone: "danger", description: "Out of stock with long lead times." },
];
