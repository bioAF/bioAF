"use client";

import { bannerText } from "@/lib/infrastructureOperations";
import { useInfrastructureOperations } from "@/hooks/useInfrastructureOperations";

/**
 * The shared infrastructure banner (mockups M3, M4, M9, M10).
 *
 * Administrators see the running operation and can open its status from any page; everyone else
 * sees only that maintenance is under way. An unconfirmed status is announced to administrators,
 * whose actions stay unavailable until it is confirmed.
 */
export function InfrastructureBanner() {
  const ops = useInfrastructureOperations();
  if (!ops) return null;
  const { active, failed, isAdmin, open } = ops;
  if (active === null && !failed) return null;

  const text = bannerText(active, isAdmin);
  if (!text) return null;

  const op = active?.busy ? active.operation : (isAdmin ? active?.interrupted?.[0] : undefined) ?? null;
  const paused = active?.state === "recovery_required";
  const link = isAdmin && op ? (active?.busy && !paused ? "View status" : "View details") : null;
  const tone = paused || failed ? "bg-red-50 border-red-300 text-red-800" : "bg-amber-50 border-amber-300 text-amber-800";

  return (
    <div
      data-testid="infrastructure-banner"
      role="status"
      className={`flex items-center justify-between gap-4 border-b px-6 py-2 text-sm ${tone}`}
    >
      <span className="font-medium">{text}</span>
      {link && op && (
        <button type="button" className="shrink-0 underline" onClick={() => open(op.id, { fromBanner: true })}>
          {link}
        </button>
      )}
    </div>
  );
}
