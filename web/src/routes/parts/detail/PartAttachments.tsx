import { useOutletContext } from "react-router-dom";
import AttachmentsPanel from "@/components/AttachmentsPanel";
import type { Part } from "@/types";

export default function PartAttachments() {
  const { part } = useOutletContext<{ part: Part }>();
  return (
    <div className="max-w-3xl">
      <AttachmentsPanel objectType="part" objectId={part.id} canWrite />
    </div>
  );
}
