import { ReactNode } from "react";

type Props = {
  title: ReactNode;
  subtitle?: ReactNode;
  idCode?: string;
  actions?: ReactNode;
};

export default function EntityHeader({ title, subtitle, idCode, actions }: Props) {
  return (
    <div className="card p-4 mb-4 flex items-start justify-between gap-4">
      <div>
        <div className="text-lg font-semibold">{title}</div>
        {subtitle && <div className="text-sm text-muted mt-0.5">{subtitle}</div>}
        {idCode && (
          <div className="mt-2 inline-block font-mono text-xs px-2 py-0.5 rounded bg-[#1f2229] text-muted">
            {idCode}
          </div>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
