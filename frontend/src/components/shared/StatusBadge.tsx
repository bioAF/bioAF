import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

/**
 * Generic status pill. Colors and labels come from the shared status registry
 * (see lib/statusStyles). Pass `entity` to render with an entity-specific
 * palette; defaults to the catch-all "generic" surface.
 */
export function StatusBadge({ status, entity = "generic" }: { status: string; entity?: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass(entity, status)}`}>
      {statusLabel(entity, status)}
    </span>
  );
}
