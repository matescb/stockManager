/**
 * `/help` — the manual's front page.
 *
 * Renders `docs/user/README.md` (the shelf index, whose own links are
 * rewritten to in-app routes by `resolveDocHref`) above a card grid of
 * every page, so the manual is browsable even if the index prose drifts.
 */
import { Link } from "react-router-dom";
import { BookOpen, Info } from "lucide-react";
import Markdown from "@/components/Markdown";
import EmptyState from "@/components/EmptyState";
import { getIndexDoc, listDocs, HELP_BASE } from "@/lib/userDocs";

export default function HelpIndex() {
  const index = getIndexDoc();
  const docs = listDocs();

  if (!index && docs.length === 0) {
    return (
      <EmptyState
        icon={BookOpen}
        title="The manual isn't in this build."
        description="Help pages are bundled at build time. Ask your administrator to redeploy."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <h1 className="text-xl font-semibold">{index?.title ?? "User help"}</h1>
        <Link to="/about" className="btn btn-sm">
          <Info size={14} />
          About this install
        </Link>
      </div>

      {index && (
        <div className="card p-4">
          <Markdown>{index.body}</Markdown>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {docs.map(doc => (
          <Link
            key={doc.slug}
            to={`${HELP_BASE}/${doc.slug}`}
            className="card p-3 hover:bg-panel2 transition-colors"
          >
            <div className="text-base font-semibold">{doc.title || doc.slug}</div>
            <div className="text-xs text-muted mt-0.5">{doc.slug}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
