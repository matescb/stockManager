/**
 * Markdown renderer for the in-app manual and the About page's
 * "Latest changes".
 *
 * Why `react-markdown` rather than a hand-rolled renderer: the surface we
 * need is small (headings, emphasis, inline code, lists, blockquote,
 * links, one table), which argues for hand-rolling — right up to the link
 * handling, which is an XSS surface. `react-markdown` never touches
 * `dangerouslySetInnerHTML` and does not parse raw HTML unless you add
 * `rehype-raw` (we don't), so HTML embedded in a doc is inert text. On top
 * of that we pin an explicit `allowedElements` allow-list and route every
 * `href` through `resolveDocHref`, which reuses the app's existing
 * `isSafeHttpOrSameOriginUrl` guard.
 *
 * Styling uses the utilities already in `src/index.css` (`card`, `table`,
 * `kbd`, `pill`) plus real Tailwind type steps, so a rendered page reads
 * as part of the app rather than a dumped README. Note this repo's Tailwind
 * scale has no `md` step, so that size name compiles to nothing here; see
 * `src/__tests__/typographyScale.test.ts`, which enforces it by scanning
 * source text — including comments, so name the step bare, never prefixed.
 */
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { resolveDocHref } from "@/lib/userDocs";

// Explicit allow-list: anything the markdown produces that isn't here is
// dropped, children and all. Deliberately excludes `img` (the shelf has no
// images — 29 screenshot placeholders are stripped upstream in `parseDoc`)
// and every embedding element.
const ALLOWED_ELEMENTS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "a",
  "strong",
  "em",
  "del",
  "code",
  "pre",
  "blockquote",
  "hr",
  "br",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
];

/**
 * GitHub-style heading slug, so `parts.md#pick-a-part-type` still lands on
 * the right section once the page is rendered in the SPA.
 */
function slugify(children: React.ReactNode): string | undefined {
  const text = flatten(children);
  if (!text) return undefined;
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function flatten(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (typeof node === "object" && "props" in (node as any)) {
    return flatten((node as any).props?.children);
  }
  return "";
}

/**
 * Link renderer enforcing the doc-shelf boundary. A target that isn't an
 * in-app help page, a same-page anchor, or a vetted http(s) URL renders as
 * plain text — the label survives, the dead (or engineer-shelf) link does
 * not. See `lib/userDocs.ts` for the policy.
 */
function DocAnchor({ href, children }: { href?: string; children?: React.ReactNode }) {
  const link = resolveDocHref(href);
  switch (link.kind) {
    case "internal":
      return (
        <Link to={link.to} className="text-accent hover:underline">
          {children}
        </Link>
      );
    case "anchor":
      return (
        <a href={link.href} className="text-accent hover:underline">
          {children}
        </a>
      );
    case "external":
      return (
        <a
          href={link.href}
          target="_blank"
          rel="noreferrer noopener"
          className="text-accent hover:underline"
        >
          {children}
        </a>
      );
    default:
      return <span>{children}</span>;
  }
}

const components: Components = {
  h1: ({ children }) => (
    <h2 id={slugify(children)} className="text-lg font-semibold mt-6 mb-2 scroll-mt-16">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 id={slugify(children)} className="text-lg font-semibold mt-6 mb-2 scroll-mt-16">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 id={slugify(children)} className="text-base font-semibold mt-5 mb-1.5 scroll-mt-16">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 id={slugify(children)} className="text-sm font-semibold mt-4 mb-1 scroll-mt-16">
      {children}
    </h4>
  ),
  p: ({ children }) => <p className="text-sm leading-relaxed my-2">{children}</p>,
  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-1 text-sm">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-1 text-sm">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children }) => (
    <code className="rounded border border-border bg-panel2 px-1 py-0.5 font-mono text-xs">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="card p-3 my-3 overflow-x-auto text-xs font-mono">{children}</pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-borderStrong pl-3 my-3 text-sm text-muted">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-5 border-border" />,
  a: DocAnchor,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="table">{children}</table>
    </div>
  ),
};

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="max-w-3xl text-text">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        allowedElements={ALLOWED_ELEMENTS}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
