// The QC quality_rating pill. Shared so the standalone QC page and the QC
// report modal render the rating identically. Colors come from the shared status
// registry (statusStyles.qcQuality); an unknown rating is a data error and stays
// red (deliberately not a neutral registry fallback).

import { statusBadgeClass, STATUS_STYLES } from "@/lib/statusStyles";

export function QualityBadge({ rating }: { rating: string }) {
  const colorClass =
    rating in STATUS_STYLES.qcQuality
      ? statusBadgeClass("qcQuality", rating)
      : "bg-red-100 text-red-700";

  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colorClass}`}>
      {rating}
    </span>
  );
}
