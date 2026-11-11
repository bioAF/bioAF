import ReactMarkdown, { type Components } from "react-markdown";

const LINK_SCHEMES = new Set(["http", "https", "mailto"]);

/**
 * The link targets a pipeline's Markdown report may keep: http, https and
 * mailto, plus relative and fragment links. Anything else, including script
 * and data URLs and their obfuscated spellings, loses its target.
 *
 * Browsers drop ASCII whitespace and control characters inside a scheme
 * (`java\tscript:`), so those are removed before the scheme is read.
 */
export function reportLinkTarget(url: string): string | undefined {
  // eslint-disable-next-line no-control-regex
  const compact = url.replace(/[\u0000- \u007f]/g, "");
  const colon = compact.indexOf(":");
  const boundary = compact.search(/[/?#]/);
  if (colon === -1 || (boundary !== -1 && boundary < colon)) return url;
  return LINK_SCHEMES.has(compact.slice(0, colon).toLowerCase()) ? url : undefined;
}

const components: Components = {
  a: ({ href, children }) => {
    if (!href) return <span>{children}</span>;
    if (href.startsWith("#")) return <a href={href}>{children}</a>;
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  // A report never loads images: a remote image is a tracking request made
  // as the viewer. The alt text keeps whatever the author meant to say.
  img: ({ alt }) => <span>{alt}</span>,
};

/**
 * A custom pipeline's `report.md`. Raw HTML in the source is skipped, never
 * rendered, and every value reaches the page as React text.
 */
export function ReportMarkdown({ source }: { source: string }) {
  return (
    <div data-testid="markdown-report" className="prose prose-sm max-w-none">
      <ReactMarkdown skipHtml urlTransform={reportLinkTarget} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
}
