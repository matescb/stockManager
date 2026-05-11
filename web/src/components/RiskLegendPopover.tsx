import { useEffect, useId, useRef, useState } from "react";
import { Info } from "lucide-react";
import type { LegendRow } from "@/lib/riskLegends";
import { riskToneClass } from "@/lib/sourcing";

type Props = {
  legend: LegendRow[];
  title: string;
};

export function RiskLegendPopover({ legend, title }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const popoverId = useId();

  useEffect(() => {
    if (!open) return undefined;

    function onPointerDown(event: PointerEvent) {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span
      ref={rootRef}
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onClick={event => event.stopPropagation()}
    >
      <button
        type="button"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-muted hover:bg-panel2 hover:text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
        aria-label={`Show ${title}`}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen(true)}
      >
        <Info className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {open && (
        <span
          id={popoverId}
          role="dialog"
          aria-label={title}
          className="absolute left-0 top-full z-50 mt-1 w-80 max-w-[calc(100vw-2rem)] rounded-md border border-border bg-panel p-3 text-left normal-case tracking-normal text-text shadow-lg"
        >
          <span className="mb-2 block text-sm font-semibold">{title}</span>
          <span className="block space-y-2">
            {legend.map(row => (
              <span key={row.label} role="listitem" className="flex items-start gap-2 text-xs">
                <span className={`pill shrink-0 ${riskToneClass(row.tone)}`}>{row.label}</span>
                <span className="text-muted">{row.description}</span>
              </span>
            ))}
          </span>
        </span>
      )}
    </span>
  );
}
