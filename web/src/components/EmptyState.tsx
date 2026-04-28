import type { LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";

type Action = { label: string; to?: string; onClick?: () => void };

type Props = {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: Action;
};

export default function EmptyState({ icon: Icon, title, description, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4">
      <div className="flex items-center justify-center w-14 h-14 rounded-lg bg-panel2 text-muted mb-4">
        <Icon size={32} strokeWidth={1.5} />
      </div>
      <div className="text-base font-medium text-text">{title}</div>
      {description && (
        <div className="mt-1 max-w-md text-sm text-muted">{description}</div>
      )}
      {action && (
        <div className="mt-4">
          {action.to ? (
            <Link to={action.to} className="btn-primary">
              {action.label}
            </Link>
          ) : (
            <button type="button" className="btn-primary" onClick={action.onClick}>
              {action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
