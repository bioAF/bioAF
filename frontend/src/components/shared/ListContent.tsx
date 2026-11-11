import type { ReactNode } from "react";
import { Card } from "@/components/ui/Card";
import { ContentLoading } from "./ContentLoading";
import { ErrorState } from "./ErrorState";

/**
 * The body of a list page, below its filters, for each state of its request:
 * the first load (a table skeleton), a first load that failed (ErrorState with
 * Retry), a successful empty answer (the page's EmptyState) and rows.
 *
 * It only arranges what the page already knows. The request, its key, polling
 * and the stale-data notice stay with the page (`useResource`, StaleNotice):
 * a refresh that keeps rows on screen passes `loading={false}` and the rows
 * stay mounted. A failed read never reaches the empty state.
 */
export function ListContent({
  loading,
  error,
  onRetry,
  empty,
  emptyState,
  children,
}: {
  /** True only while there is no answer yet for the current selection. */
  loading: boolean;
  /** A failed first load's safe message; null once any answer is on screen. */
  error: string | null;
  onRetry: () => void;
  empty: boolean;
  emptyState: ReactNode;
  children: ReactNode;
}) {
  if (loading) return <ContentLoading variant="table" />;
  if (error) {
    return (
      <Card padding="none">
        <ErrorState message={error} onRetry={onRetry} />
      </Card>
    );
  }
  if (empty) return <>{emptyState}</>;
  return <>{children}</>;
}
