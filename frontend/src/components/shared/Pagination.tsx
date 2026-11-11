import { Button } from "@/components/ui/Button";
import { PageSizeSelect } from "./PageSizeSelect";

/**
 * Previous/next paging for a server-paged list, with an optional rows-per-page
 * choice (25/50/100, from PageSizeSelect).
 *
 * Controlled: the page owns `page` and `pageSize` and puts them in its query.
 * Changing the size, a filter or the sort is where the page resets itself to
 * page 1; this component asks for no requests of its own. With one page or
 * none there is nothing to page through, so only the size choice shows.
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  /** Omit for a list whose page size is fixed. */
  onPageSizeChange?: (size: number) => void;
}) {
  const totalPages = Math.ceil(total / pageSize);
  const paged = totalPages > 1;
  if (!paged && !onPageSizeChange) return null;

  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <nav aria-label="Pagination" className="mt-4 flex flex-wrap items-center justify-between gap-3">
      {onPageSizeChange ? <PageSizeSelect value={pageSize} onChange={onPageSizeChange} /> : <span />}
      {paged && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-ink-subtle">
            Showing {first}–{last} of {total}
          </p>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => onPageChange(page - 1)} disabled={page <= 1}>
              Previous
            </Button>
            <span className="py-1 text-sm text-ink-subtle">
              Page {page} of {totalPages}
            </span>
            <Button variant="secondary" size="sm" onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}>
              Next
            </Button>
          </div>
        </div>
      )}
    </nav>
  );
}
