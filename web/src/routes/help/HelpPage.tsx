/**
 * `/help/:slug` — one manual page.
 *
 * Content comes from the bundled `docs/user/` shelf; an unknown slug gets
 * the same empty-state treatment the rest of the app uses rather than a
 * blank card. Anchors inside the page work because `Markdown` slugifies
 * headings the same way GitHub does, which is what
 * `projects-and-bom.md`'s one `parts.md#pick-a-part-type` link expects.
 */
import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { BookOpen } from "lucide-react";
import Markdown from "@/components/Markdown";
import EmptyState from "@/components/EmptyState";
import { getDoc, HELP_BASE } from "@/lib/userDocs";

export default function HelpPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const doc = getDoc(slug);

  // Scroll a fresh page to the top; react-router keeps the browser's
  // scroll offset across a same-layout navigation, which lands you
  // mid-article when you click a page in the rail.
  useEffect(() => {
    if (!window.location.hash) window.scrollTo({ top: 0 });
  }, [slug]);

  if (!doc) {
    return (
      <EmptyState
        icon={BookOpen}
        title="No such help page."
        description={`There's no manual page called "${slug}".`}
        action={{ label: "Back to the manual", to: HELP_BASE }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{doc.title || slug}</h1>
      <div className="card p-4">
        <Markdown>{doc.body}</Markdown>
      </div>
      <div className="text-sm">
        <Link to={HELP_BASE} className="text-accent hover:underline">
          ← All help pages
        </Link>
      </div>
    </div>
  );
}
