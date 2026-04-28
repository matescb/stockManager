import { useOutletContext } from "react-router-dom";
import ActivityTimeline from "@/components/ActivityTimeline";
import type { Part } from "@/types";

export default function PartActivity() {
  const { part } = useOutletContext<{ part: Part }>();
  return (
    <div className="max-w-3xl">
      <ActivityTimeline endpoint={`/parts/${part.id}/activity`} />
    </div>
  );
}
