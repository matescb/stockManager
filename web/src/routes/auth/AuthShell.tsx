import { ReactNode } from "react";
import Brand from "@/components/Brand";

type Props = {
  title: string;
  children: ReactNode;
};

export default function AuthShell({ title, children }: Props) {
  return (
    <div className="min-h-full grid lg:grid-cols-2 bg-bg">
      <aside className="hidden lg:flex flex-col justify-between bg-panel border-r border-border p-10">
        <div className="flex items-center gap-3">
          <Brand />
        </div>

        <div className="max-w-md">
          <h2 className="text-3xl font-semibold tracking-tight leading-tight">
            Self-hosted parts &<br /> production manager.
          </h2>
          <p className="mt-4 text-muted text-sm leading-relaxed">
            Track parts, storage, lots and serials. Run purchase orders against
            an append-only stock ledger. Build from a project's BOM with
            substitute fallback. Reports for low-stock, inventory value, and
            soon-to-expire lots come out of the box.
          </p>
        </div>

        <div className="text-xs text-muted">
          Open-source · workspace-isolated · runs on your hardware.
        </div>
      </aside>

      <main className="flex flex-col items-center justify-center px-6 py-10">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex justify-center mb-6">
            <Brand />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight mb-6">{title}</h1>
          {children}
        </div>
      </main>
    </div>
  );
}
