import { Boxes } from "lucide-react";

type Props = { compact?: boolean };

export default function Brand({ compact }: Props) {
  return (
    <span className="inline-flex items-center gap-2 select-none">
      <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-accent/15 text-accent">
        <Boxes size={16} strokeWidth={2.25} />
      </span>
      {!compact && (
        <span className="font-semibold text-text tracking-tight">stockmgr</span>
      )}
    </span>
  );
}
