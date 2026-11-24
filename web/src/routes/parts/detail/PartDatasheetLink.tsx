import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { humanSize } from "@/components/AttachmentsPanel";
import type { Attachment, Part } from "@/types";
import { resolveDatasheetLink } from "./datasheetLink";

type Props = {
  part: Pick<Part, "id" | "mpn" | "name">;
  /** The part's `datasheet_url` custom field (the upstream URL). */
  datasheetUrl: string | null;
};

/**
 * "Datasheet" card on the part Info tab. Prefers the stored local copy
 * (a `file_type='datasheet'` attachment) and keeps the upstream URL as a
 * secondary "source" link. Without a local copy it links upstream and
 * says the file has not been downloaded yet.
 *
 * Shares its query key with `AttachmentsPanel`, so the Attachments tab
 * and this card read one cache entry.
 */
export default function PartDatasheetLink({ part, datasheetUrl }: Props) {
  const attachmentsQuery = useQuery({
    queryKey: useWsKey("attachments", "part", part.id),
    queryFn: ({ signal }) =>
      api.get<Attachment[]>(`/attachments/by-object/part/${part.id}`, { signal }),
  });
  // Wait for the attachments list so the link never flips from the
  // provider URL to the local copy under the user's cursor. On error the
  // list is undefined and the provider URL is used.
  if (attachmentsQuery.isPending) return null;
  const link = resolveDatasheetLink(attachmentsQuery.data, datasheetUrl, part.mpn || part.name);
  if (!link) return null;

  if (link.kind === "local") {
    const meta = [link.fileName, link.sizeBytes != null ? humanSize(link.sizeBytes) : null]
      .filter(Boolean)
      .join(" · ");
    return (
      <div className="card p-4 col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <a
          href={link.href}
          className="inline-flex items-center gap-1.5 text-accent hover:underline"
          title="Download the stored copy"
        >
          <FileText size={14} aria-hidden="true" /> Datasheet
          <Download size={12} aria-hidden="true" />
        </a>
        {meta && <span className="text-xs text-muted">{meta}</span>}
        {link.sourceUrl && (
          <a
            href={link.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-accent hover:underline"
          >
            source <ExternalLink size={11} aria-hidden="true" />
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="card p-4 col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <a
        href={link.href}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-accent hover:underline"
      >
        <FileText size={14} aria-hidden="true" /> Datasheet <ExternalLink size={12} aria-hidden="true" />
      </a>
      {attachmentsQuery.isSuccess && (
        <span className="text-xs text-muted">Not downloaded yet — opens the provider's copy.</span>
      )}
    </div>
  );
}
